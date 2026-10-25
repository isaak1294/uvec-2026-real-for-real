#!/usr/bin/env python3
"""Online emergency-vehicle dispatcher (minute-by-minute simulation).

Usage: python dispatcher.py input.txt [--mode standby|base] [--tie-window M]
                                      [--no-replan] [--backlog plan|oldest]
                                      [--age-weight A] [--explain] [--stats]
"""
# Rules:
#
# Objective: minimize total response delay = sum over calls of
# (arrival on scene - call time).
#
# The key quantity is a vehicle's "reach" to a spot:
#   (minutes until it is free, including queued jobs) + (distance from where it
#   will be free to that spot)
#   - Dispatch: give calls to whichever vehicles reach them soonest. All queued
#     calls are re-planned together: every split for a few calls, dynamic
#     programming for up to 12, local search beyond that.
#   - Coverage: average over past call spots of the best reach to each.
#   - Standby: park idle vehicles where they give the best coverage (k-median).
#   - Ties between equally fast plans go to the one with the best coverage.
import argparse
import sys
from itertools import permutations, product

# Constants

BASE = (0, 0) # (east, north)
MIDNIGHT = 24 * 60 # no call comes in from here on
PRIOR_WEIGHT = 3.0 # base counts as this many past calls (until data arrives)
HYSTERESIS = 1.0 # only reposition if avg coverage improves by >= 1 min
EXACT_MAX_CALLS = 7 # try every split when at most this many calls wait
EXACT_MAX_PLANS = 200_000 # ... and vehicles**calls stays below this
DP_MAX_CALLS = 12 # beyond that, exact by dynamic programming up to this many
AGE_WEIGHT = 1.0 # in those backlogs a call counts 1 + this x hours waited


class Call:
    """One emergency call: id, time it came in, location, job length."""
    def __init__(self, cid, t, pos, dur):
        self.id = cid
        self.t = t # minutes since 00:00
        self.pos = pos # (east, north)
        self.dur = dur # minutes of work on scene


class InputError(ValueError):
    """Unexpected format; the message names the line."""


def parse_text(text):
    # Read "vehicles" then one "HH:MM [d N|S] [d E|W] minutes" line per call,
    # in time order. Raises InputError on anything else.
    # Returns (number of vehicles, [Call, ...]).

    # keep each non-blank line with its line number, for error messages
    rows = []
    for n, line in enumerate(text.splitlines(), 1):
        if line.strip():
            rows.append((n, line.split()))
    if not rows:
        raise InputError("the input is empty")

    # first line: number of vehicles
    n, head = rows[0]
    if len(head) != 1 or not head[0].isdigit() or int(head[0]) < 1:
        raise InputError(f"line {n}: expected the number of vehicles (a whole number, "
                         f"at least 1), got '{' '.join(head)}'")

    step_of = {"N": (0, 1), "S": (0, -1), "E": (1, 0), "W": (-1, 0)}
    calls = []
    last = None # time of the previous call, to check the order
    for n, r in rows[1:]:
        def bad(why):
            return InputError(f"line {n}: {why}: '{' '.join(r)}'")

        # time "HH:MM"
        hm = r[0].split(":")
        is_time = len(hm) == 2 and all(s.isdigit() for s in hm)
        if not is_time or int(hm[0]) > 23 or int(hm[1]) > 59:
            raise bad("expected a 24-hour time like 09:05 first")

        # job length is the last number
        if len(r) < 2 or not r[-1].isdigit():
            raise bad("expected the job length in whole minutes last")

        # everything in between is the location: "15 N 20 W", "4 E" or nothing
        legs = r[1:-1]
        if len(legs) % 2 or len(legs) > 4:
            raise bad("expected at most two distances like '15 N 20 W' between "
                      "the time and the job length")
        x = y = 0
        axes = set() # axes already given, so "5 N 3 S" is rejected
        for k in range(0, len(legs), 2):
            d = legs[k]
            c = legs[k + 1].upper()
            if not d.isdigit() or c not in step_of:
                raise bad(f"expected a whole number and N, S, E or W, got '{legs[k]} {legs[k + 1]}'")
            axis = "north/south" if c in "NS" else "east/west"
            if axis in axes:
                raise bad(f"two {axis} distances")
            axes.add(axis)
            dx, dy = step_of[c]
            x += int(d) * dx
            y += int(d) * dy

        # calls must come in time order (so none after midnight)
        t = int(hm[0]) * 60 + int(hm[1])
        if last is not None and t < last:
            raise bad(f"{r[0]} is earlier than the call before it ({fmt(last)}); calls "
                      f"come in time order, none after midnight")
        last = t

        calls.append(Call(len(calls), t, (x, y), int(r[-1]))) # id = position in the file
    return int(head[0]), calls


# Grid helpers

def dist(a, b):
    # Manhattan distance = travel minutes between two intersections
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def step(pos, dest):
    # Move one intersection toward dest: north/south leg first, then east/west.
    x, y = pos
    tx, ty = dest
    if y != ty:
        return (x, y + (1 if ty > y else -1))
    if x != tx:
        return (x + (1 if tx > x else -1), y)
    return pos # already there


def fmt(t):
    # minutes -> "HH:MM", wrapping at midnight
    return f"{t // 60 % 24:02d}:{t % 60:02d}"


def spot(p):
    # A location the way the input writes it: '15 N 20 W', or 'base'.
    parts = []
    if p[1]:
        parts.append(f"{abs(p[1])} {'N' if p[1] > 0 else 'S'}")
    if p[0]:
        parts.append(f"{abs(p[0])} {'E' if p[0] > 0 else 'W'}")
    return " ".join(parts) or "base"


def drive_text(a, b):
    # "Drive 3 minutes north and 2 minutes east", or None if a == b
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    parts = []
    if dy:
        parts.append(f"{abs(dy)} minutes {'north' if dy > 0 else 'south'}")
    if dx:
        parts.append(f"{abs(dx)} minutes {'east' if dx > 0 else 'west'}")
    if not parts:
        return None
    return "Drive " + " and ".join(parts)


# Queueing calls

def run_sequence(t, pos, seq):
    # Serve calls in order, starting free at time t at pos.
    # Returns (total delay, max delay, time free afterwards, place free afterwards).
    total = 0
    worst = 0
    for c in seq:
        arrive = max(t, c.t) + dist(pos, c.pos) # can't leave for a call before it comes in
        total += arrive - c.t
        worst = max(worst, arrive - c.t)
        t = arrive + c.dur # free again once the job is done
        pos = c.pos
    return total, worst, t, pos


# BACKLOG PLANNING

# Every waiting call has already come in and no vehicle is free before now,
# so a vehicle never waits for a call: each arrival is just the start time
# plus the legs and jobs before it. That means a queue's cost is a sum over
# its legs (each leg delays every call still to come), which both the dynamic
# program and cheapest insertion rely on.
# Costs are weighted: w[call id] = 1 + age weight x hours the call has waited,
# so a big backlog can't keep pushing its oldest calls back.

def queue_cost(t, pos, seq, w):
    # Weighted delay of serving seq in order from (t, pos). Calls are already in.
    total = 0
    for c in seq:
        t += dist(pos, c.pos) # arrive (no waiting: the call is already in)
        total += w[c.id] * (t - c.t)
        t += c.dur # do the job
        pos = c.pos
    return total


def dp_plan(starts, pending, w):
    # Least weighted delay split and orders, exactly, by dynamic programming.
    # starts = [(time free, place free)] per vehicle.
    # Returns, per vehicle, the order (as pending indices) it should serve.

    # A set of calls is a bitmask: bit i set = pending[i] is in the set.
    k = len(pending)
    INF = float("inf")
    n_sets = 1 << k # number of subsets of k calls
    pos = [c.pos for c in pending]

    # gap[j][n]: minutes from arriving at call j to arriving at call n
    # (do j's job, then drive j -> n)
    gap = []
    for c in pending:
        gap.append([c.dur + dist(c.pos, q) for q in pos])

    # members[S]: the calls in set S, lowest index first
    # weight[S]: total weight of the calls in S
    members = [[] for _ in range(n_sets)]
    weight = [0] * n_sets
    for S in range(1, n_sets):
        low = (S & -S).bit_length() - 1 # index of S's lowest call
        without_low = S ^ (1 << low)
        members[S] = [low] + members[without_low]
        weight[S] = weight[without_low] + w[pending[low].id]

    # Phase 1: best order within a set, not caring where the vehicle starts.
    G = [[INF] * k for _ in range(n_sets)]
    after = [[-1] * k for _ in range(n_sets)]
    for S in range(1, n_sets):
        for j in members[S]:
            rest = S ^ (1 << j) # S without j
            if rest == 0: # j is alone: nothing after it
                G[S][j] = 0
                continue
            best = INF
            best_next = -1
            for n in members[rest]:
                c = weight[rest] * gap[j][n] + G[rest][n]
                if c < best:
                    best = c
                    best_next = n
            G[S][j] = best
            after[S][j] = best_next

    # Phase 2: add where each vehicle starts.
    cost = []
    first = []
    for t0, p0 in starts:
        reach = [t0 + dist(p0, p) for p in pos] # when this vehicle could reach each call
        cost_v = [0] * n_sets
        first_v = [-1] * n_sets
        for S in range(1, n_sets):
            best = INF
            best_first = -1
            for j in members[S]:
                c = weight[S] * reach[j] + G[S][j]
                if c < best:
                    best = c
                    best_first = j
            cost_v[S] = best
            first_v[S] = best_first
        cost.append(cost_v)
        first.append(first_v)

    # Phase 3: split the calls between vehicles.
    V = len(starts)
    full = n_sets - 1 # the set of all pending calls
    H = [[INF] * n_sets for _ in range(V)]
    take = [[0] * n_sets for _ in range(V)]
    H[V - 1] = cost[V - 1][:] # the last vehicle takes whatever is left
    take[V - 1] = list(range(n_sets))
    for v in range(V - 2, -1, -1):
        # vehicle 0 only ever faces the full set
        sets_to_fill = [full] if v == 0 else range(n_sets)
        for M in sets_to_fill:
            best_cost = H[v + 1][M] # option: v takes nothing
            best_sub = 0
            sub = M
            while sub: # try every non-empty subset of M for vehicle v
                c = cost[v][sub] + H[v + 1][M ^ sub]
                if c < best_cost:
                    best_cost = c
                    best_sub = sub
                sub = (sub - 1) & M # next smaller subset of M
            H[v][M] = best_cost
            take[v][M] = best_sub

    # Phase 4: read the answer back out.
    orders = []
    left = full # calls not given to a vehicle yet
    for v in range(V):
        S = take[v][left] # the calls vehicle v serves
        left ^= S
        order = []
        j = first[v][S]
        while S: # follow the "after" links
            order.append(j)
            nxt = after[S][j]
            S ^= 1 << j
            j = nxt
        orders.append(order)
    return orders


def cheapest_insertion(t0, p0, seq, c, w):
    free_time = [t0]
    free_pos = [p0]
    for q in seq:
        free_time.append(free_time[-1] + dist(free_pos[-1], q.pos) + q.dur)
        free_pos.append(q.pos)

    # behind[i]: total weight of the calls from slot i on (all pushed back by c)
    behind = [0] * (len(seq) + 1)
    for i in range(len(seq) - 1, -1, -1):
        behind[i] = behind[i + 1] + w[seq[i].id]

    best = None
    for i in range(len(seq) + 1):
        leg = dist(free_pos[i], c.pos)
        added = w[c.id] * (free_time[i] + leg - c.t) # c's own weighted delay
        if i < len(seq):
            # the detour through c pushes everything after slot i back
            nxt = seq[i].pos
            detour = leg + c.dur + dist(c.pos, nxt) - dist(free_pos[i], nxt)
            added += behind[i] * detour
        if best is None or added < best[0]:
            best = (added, i)
    return best


def local_search(starts, pending, w, max_rounds=50):
    # Backlogs too big to solve exactly: put calls (oldest first) where they
    # add the least delay, then move single calls and swap pairs between
    # vehicles while the cost drops. Returns a queue (calls) per vehicle.
    V = len(starts)
    queues = [[] for _ in range(V)]

    # Starting plan: each call goes to the vehicle and slot where it adds the least
    for c in pending:
        options = []
        for v in range(V):
            added, i = cheapest_insertion(*starts[v], queues[v], c, w)
            options.append((added, i, v))
        _, i, v = min(options)
        queues[v].insert(i, c)
    cost = [queue_cost(*starts[v], queues[v], w) for v in range(V)]

    for _ in range(max_rounds):
        improved = False

        # Move: take one call out and re-insert it wherever is cheapest (any vehicle)
        for a in range(V):
            i = 0
            while i < len(queues[a]):
                c = queues[a][i]
                rest = queues[a][:i] + queues[a][i + 1:] # a's queue without c
                rest_cost = queue_cost(*starts[a], rest, w)
                options = []
                for b in range(V):
                    target = rest if b == a else queues[b]
                    added, j = cheapest_insertion(*starts[b], target, c, w)
                    options.append((added, j, b))
                added, j, b = min(options)
                if added < cost[a] - rest_cost - 1e-9: # cheaper than where c is now
                    queues[a] = rest
                    queues[b].insert(j, c)
                    cost[a] = queue_cost(*starts[a], queues[a], w)
                    cost[b] = queue_cost(*starts[b], queues[b], w)
                    improved = True
                    if b == a and j <= i:
                        i += 1 # c moved earlier in this queue: next unchecked call is at i + 1
                    continue # otherwise the next unchecked call slid into slot i
                i += 1

        # Swap: exchange one call of vehicle a with one call of vehicle b
        for a in range(V):
            for b in range(a + 1, V):
                for i in range(len(queues[a])):
                    for j in range(len(queues[b])):
                        qa = queues[a][:]
                        qb = queues[b][:]
                        qa[i], qb[j] = qb[j], qa[i]
                        ca = queue_cost(*starts[a], qa, w)
                        cb = queue_cost(*starts[b], qb, w)
                        if ca + cb < cost[a] + cost[b] - 1e-9:
                            queues[a] = qa
                            queues[b] = qb
                            cost[a] = ca
                            cost[b] = cb
                            improved = True

        if not improved:
            break
    return queues


# Define Vehicles

class Vehicle:
    """A vehicle's position, state, current job and queue."""
    def __init__(self, vid):
        self.id = vid
        self.pos = BASE
        self.state = "idle" # idle | to_job | on_job
        self.job = None # call being driven to / worked on
        self.finish_at = None # when the current job ends (on_job)
        self.dest = None # where an idle vehicle is driving (None = parked)
        self.queue = [] # calls planned after the current job

    def available(self, now):
        # (time, place) at which this vehicle could start driving to a new call
        if self.state == "to_job":
            # finish the drive, then the job
            return now + dist(self.pos, self.job.pos) + self.job.dur, self.job.pos
        if self.state == "on_job":
            return self.finish_at, self.job.pos
        return now, self.pos # idle: right now, from where it is

    def free_after_queue(self, now):
        # (time, place) free after the current job AND everything queued
        t, p = self.available(now)
        _, _, t, p = run_sequence(t, p, self.queue)
        return t, p

    def snapshot(self):
        # [x, y, state(, dest x, dest y)] for the web replay. States: p = parked,
        # m = moving while idle (to standby or base), d = driving to a job, j = on a job.
        if self.state == "to_job":
            return [*self.pos, "d", *self.job.pos]
        if self.state == "on_job":
            return [*self.pos, "j"]
        if self.dest is not None:
            return [*self.pos, "m", *self.dest]
        return [*self.pos, "p"]


# Define dispatcher:

class Dispatcher:
    """Runs the day: plans calls, moves vehicles, logs events."""
    def __init__(self, n_vehicles, mode="standby", tie_window=0, replan=True,
                 backlog="plan", age_weight=AGE_WEIGHT):
        self.fleet = [Vehicle(i) for i in range(n_vehicles)]
        self.mode = mode # "standby" or "base"
        self.tie_window = tie_window # extra delay minutes allowed for better coverage
        self.replan_all = replan # False = baseline: queued calls never move
        self.backlog = backlog # "plan" or "oldest"
        self.age_weight = age_weight
        self.unassigned = [] # calls that arrived but have no vehicle yet
        self.seen = [] # every call so far (the demand map for coverage)
        self.events = [] # (time, vehicle id, sequence no., text)
        self.delay = {} # call id -> response delay
        self.travel = 0 # total minutes driven by the fleet
        self.notes = [] # (time, text): why each decision was made (--explain)
        self.planned = {} # call id -> vehicle it was last planned on
        self.served = {} # call id -> {"vehicle", "dispatch", "arrive", "finish"}
        self.frames = [] # (time, [vehicle snapshot, ...]) at the end of each minute
        self.sent_home = False # the "past midnight" note was written

    def log(self, t, v, text):
        # schedule line; len(events) keeps lines in the order they happened
        self.events.append((t, v.id, len(self.events), text))

    def note(self, t, text):
        self.notes.append((t, text))

    def demand(self):
        # where calls tend to come from: every past call (weight 1) + base (prior)
        pts = [(c.pos, 1.0) for c in self.seen]
        pts.append((BASE, PRIOR_WEIGHT))
        return pts

    @staticmethod
    def coverage(now, avail, pts):
        # Weighted average over past call spots of the soonest any vehicle
        # could reach that spot. avail = [(time free, place free), ...]
        total_w = sum(w for _, w in pts)
        total = 0
        for q, w in pts:
            soonest = min(t - now + dist(p, q) for t, p in avail)
            total += w * soonest
        return total / total_w

    # Plan dispatches:

    def plan(self, now):
        # Decide which vehicle serves each call nobody is driving to yet.
        if self.replan_all:
            # queued calls go back on the table too
            pending = self.unassigned + [c for v in self.fleet for c in v.queue]
            for v in self.fleet:
                v.queue = []
        else:
            # baseline: once queued, a call stays put
            pending = self.unassigned
        self.unassigned = []
        if not pending:
            return
        pending.sort(key=lambda c: (c.t, c.id)) # oldest first

        # for --explain: when each vehicle would arrive if it took just this call
        alone = {}
        for c in pending:
            arrivals = []
            for v in self.fleet:
                t, p = v.free_after_queue(now)
                arrivals.append((max(t, c.t) + dist(p, c.pos), v.id))
            alone[c.id] = sorted(arrivals)

        # pick a planner by how many calls are waiting
        V = len(self.fleet)
        k = len(pending)
        few_enough = k <= EXACT_MAX_CALLS and V ** k <= EXACT_MAX_PLANS
        if self.replan_all and few_enough:
            how = self.plan_exact(now, pending) # try every split
        elif self.replan_all and self.backlog == "plan":
            how = self.plan_backlog(now, pending) # DP or local search
        else:
            self.plan_greedy(now, pending) # oldest first to the soonest vehicle
            if k == 1:
                how = None
            else:
                prefix = "too many to try every split, so " if self.replan_all else ""
                how = prefix + f"{k} waiting calls each join, oldest first, " \
                               f"the vehicle that reaches them soonest"
        self.explain_plan(now, pending, alone, how)

    def plan_greedy(self, now, pending):
        # Each call (oldest first) joins the queue of the vehicle that reaches it soonest.
        for c in pending:
            def reach(v):
                t, p = v.free_after_queue(now)
                return (max(t, c.t) + dist(p, c.pos), v.id) # ties -> lower vehicle id
            best = min(self.fleet, key=reach)
            best.queue.append(c)

    def plan_exact(self, now, pending):
        # Try every split of the pending calls among vehicles, with the best
        # order within each vehicle, and keep the lowest total delay.
        # Returns the explain note.
        V = len(self.fleet)
        k = len(pending)
        start = [v.available(now) for v in self.fleet]

        # Step 1: for each vehicle and each subset of calls (bitmask), the best order.
        # best[vi][mask] = (delay, max delay, order, free time, free place)
        best = [dict() for _ in range(V)]
        for vi, (t0, p0) in enumerate(start):
            best[vi][0] = (0, 0, (), t0, p0) # no calls: free right away, where it is
            for mask in range(1, 1 << k):
                items = [i for i in range(k) if mask >> i & 1] # calls in this subset
                cand = None
                for order in permutations(items):
                    seq = [pending[i] for i in order]
                    d, mx, t, p = run_sequence(t0, p0, seq)
                    if cand is None or (d, mx) < cand[:2]: # lower delay, then lower max
                        cand = (d, mx, order, t, p)
                best[vi][mask] = cand

        # Step 2: every way to give each call to a vehicle
        plans = []
        for assign in product(range(V), repeat=k): # assign[i] = vehicle for call i
            masks = [0] * V # masks[vi] = calls vehicle vi got
            for i, vi in enumerate(assign):
                masks[vi] |= 1 << i
            parts = [best[vi][masks[vi]] for vi in range(V)]
            total = sum(x[0] for x in parts)
            worst = max(x[1] for x in parts)
            plans.append((total, worst, assign, parts))

        # Step 3: among plans within tie_window of the best delay, prefer best
        # coverage, then lower delay, lower worst-case delay, lower vehicle numbers.
        lowest = min(p[0] for p in plans)
        pts = self.demand()

        def key(p):
            total, worst, assign, parts = p
            free_spots = [(x[3], x[4]) for x in parts] # where/when each vehicle ends up free
            cov = self.coverage(now, free_spots, pts)
            return (round(cov, 6), total, worst, assign)

        near = [p for p in plans if p[0] <= lowest + self.tie_window]
        chosen = min(near, key=key)
        for vi, part in enumerate(chosen[3]):
            order = part[2]
            self.fleet[vi].queue = [pending[i] for i in order]

        # explain note
        if len(plans) == 1:
            return None
        if k == 1:
            how = f"tried each of the {V} vehicles"
        else:
            how = f"tried all {len(plans)} ways to split {k} waiting calls among {V} " \
                  f"vehicles, each in its best order"
        how += f": total wait {chosen[0]} min"
        if len(near) > 1:
            cov = self.coverage(now, [(x[3], x[4]) for x in chosen[3]], pts)
            extra = chosen[0] - lowest
            if extra:
                how += f" ({extra} min more than the fastest plan)"
            if not self.tie_window:
                how += f"; {len(near)} plans tie"
            else:
                how += f"; {len(near)} plans within {self.tie_window} min of the best"
            how += f", so the one leaving the best coverage won (avg reach to past call " \
                   f"spots {cov:.1f} min)"
        else:
            next_best = min(p[0] for p in plans if p is not chosen)
            how += f", next best {next_best}"
        return how

    def plan_backlog(self, now, pending):
        # Too many calls to try every split: dynamic programming (still exact)
        # up to DP_MAX_CALLS, local search beyond. Each call's delay is weighted
        # by how long it has waited already. Returns the explain note.
        starts = [v.available(now) for v in self.fleet]
        w = {} # call id -> weight: 1 + age_weight x hours waited
        for c in pending:
            w[c.id] = 1 + self.age_weight * (now - c.t) / 60
        k = len(pending)
        if k <= DP_MAX_CALLS:
            orders = dp_plan(starts, pending, w)
            queues = [[pending[i] for i in order] for order in orders]
            how = f"{k} waiting calls: best split and order among {len(self.fleet)} " \
                  f"vehicles found exactly by dynamic programming"
        else:
            queues = local_search(starts, pending, w)
            how = f"{k} waiting calls, too many to solve exactly: placed where each adds " \
                  f"the least delay, then moved and swapped until nothing helps"
        if self.age_weight:
            how += f" (a call counts 1 + {self.age_weight:g} x hours it has waited)"

        # for the note: what oldest-first would have cost (unweighted)
        ones = {c.id: 1 for c in pending}
        for v in self.fleet:
            v.queue = []
        self.plan_greedy(now, pending)
        fifo = sum(queue_cost(*s, v.queue, ones) for s, v in zip(starts, self.fleet))

        # use the real plan
        for v, q in zip(self.fleet, queues):
            v.queue = q
        total = sum(queue_cost(*s, q, ones) for s, q in zip(starts, queues))
        return f"{how}: total wait {total} min (oldest first: {fifo})"

    def explain_plan(self, now, pending, alone, how):
        # Note the plan, then each call that is new or moved: where it went,
        # when that vehicle gets there, and when every vehicle could have.
        if how:
            self.note(now, how)
        ids = {c.id for c in pending}
        for v in self.fleet:
            t, p = v.available(now)
            ahead = 0 if v.state == "idle" else 1 # jobs before the next queued one
            for c in v.queue:
                arrive = max(t, c.t) + dist(p, c.pos)
                was = self.planned.get(c.id) # vehicle it was on before (None = new call)
                if c.id in ids and was != v.id:
                    if ahead == 0:
                        when = "leaves now"
                    else:
                        when = f"after {ahead} job{'s' if ahead > 1 else ''}"
                    moved = "" if was is None else f" (moved from vehicle {was})"
                    every = ", ".join(f"V{vid} {fmt(a)}" for a, vid in alone[c.id])
                    self.note(now, f"call {c.id} ({spot(c.pos)}, came {fmt(c.t)}) -> vehicle "
                                   f"{v.id}{moved}, {when}, on scene {fmt(arrive)}, waits "
                                   f"{arrive - c.t} min | each vehicle alone: {every}")
                    self.planned[c.id] = v.id
                t = arrive + c.dur
                p = c.pos
                ahead += 1

    # Actually send out vehicles, commit them to routes.

    def commit(self, now):
        # Idle vehicles with a planned call leave for it right away.
        for v in self.fleet:
            if v.state == "idle" and v.queue:
                self.dispatch(v, v.queue.pop(0), now)

    def dispatch(self, v, c, now):
        # v starts driving to call c (from wherever it is, even mid-return)
        v.state = "to_job"
        v.job = c
        v.dest = None # cancels any trip to base/standby
        self.served[c.id] = {"vehicle": v.id, "dispatch": now}
        self.log(now, v, "Start route to job")
        text = drive_text(v.pos, c.pos)
        if text:
            self.log(now, v, text)
        else: # already standing on the incident
            self.start_job(v, now)

    def start_job(self, v, now):
        self.log(now, v, "Start job")
        self.delay[v.job.id] = now - v.job.t
        self.served[v.job.id]["arrive"] = now
        v.state = "on_job"
        v.finish_at = now + v.job.dur

    # Controlling idle vehicles:

    def place(self, p):
        return "base" if p == BASE else "standby"

    def reposition(self, now):
        # Send idle vehicles (nothing queued) to base or to standby spots.
        idle = [v for v in self.fleet if v.state == "idle"]
        if not idle:
            return
        home = now >= MIDNIGHT # no more calls can come in
        if home and not self.sent_home:
            self.sent_home = True
            self.note(now, "past midnight no more calls can come in, so each vehicle "
                           "heads home once its calls are done")
        if self.mode == "base" or home:
            targets = {v.id: BASE for v in idle}
            gain = None
        else:
            found = self.standby_targets(now, idle)
            if found is None: # not worth moving
                return
            targets = found[0]
            gain = found[1:] # (coverage now, coverage after)

        moves = []
        for v in idle:
            target = targets[v.id]
            current = v.dest if v.dest is not None else v.pos
            if target == current or target == v.pos:
                continue # already there / heading there
            v.dest = target
            self.log(now, v, f"Start route to {self.place(target)}")
            self.log(now, v, drive_text(v.pos, target))
            moves.append(f"vehicle {v.id} -> {spot(target)}")
        if moves and gain:
            n = len(self.seen)
            self.note(now, f"standby: {', '.join(moves)}; average reach to the {n} past call "
                           f"spot{'s' if n > 1 else ''} {gain[0]:.1f} -> {gain[1]:.1f} min")

    def standby_targets(self, now, idle):
        # k-median
        import numpy as np
        pts = self.demand()
        Q = np.array([q for q, _ in pts]) # demand spots, one row each
        W = np.array([w for _, w in pts]) # their weights

        # candidate spots: every (x, y) with x from some demand spot and y from some demand spot
        xs = np.unique(Q[:, 0])
        ys = np.unique(Q[:, 1])
        cands = np.array([(x, y) for x in xs for y in ys])
        # D[c][q]: distance from candidate c to demand spot q
        D = np.abs(cands[:, None, :] - Q[None, :, :]).sum(-1)

        # busy[q]: soonest a busy vehicle (after its queue) could reach demand spot q
        busy = np.full(len(Q), np.inf)
        for v in self.fleet:
            if v.state != "idle":
                t, p = v.free_after_queue(now)
                reach = t - now + np.abs(Q - np.array(p)).sum(1)
                busy = np.minimum(busy, reach)

        def best_addition(cur):
            # cur[q] = best reach to q so far. Which candidate helps most if a
            # vehicle parks there? Returns (candidate, new weighted total).
            scores = (W * np.minimum(cur, D)).sum(1)
            c = int(np.argmin(scores))
            return c, scores[c]

        # greedy: place idle vehicles one at a time at the best spot
        chosen = []
        cur = busy.copy()
        score = None
        for _ in idle:
            c, score = best_addition(cur)
            chosen.append(c)
            cur = np.minimum(cur, D[c])

        # swaps: re-place one vehicle at a time while that helps (max 5 rounds)
        for _ in range(5):
            improved = False
            for i in range(len(chosen)):
                rest = busy.copy() # coverage from everyone except spot i
                for j, c in enumerate(chosen):
                    if j != i:
                        rest = np.minimum(rest, D[c])
                c, s = best_addition(rest)
                if s < score - 1e-9:
                    chosen[i] = c
                    score = s
                    improved = True
            if not improved:
                break

        # coverage if nobody moves (idle vehicles stay where they are / are heading)
        stay = busy.copy()
        for v in idle:
            p = v.dest if v.dest is not None else v.pos
            stay = np.minimum(stay, np.abs(Q - np.array(p)).sum(1))
        if (W * stay).sum() - score < HYSTERESIS * W.sum():
            return None # gain under HYSTERESIS min on average: not worth the driving

        spots = [tuple(int(a) for a in cands[c]) for c in chosen]
        # match vehicles to spots, minimizing total driving
        if len(idle) <= 6:
            # try every matching
            order = min(permutations(spots),
                        key=lambda o: sum(dist(v.pos, s) for v, s in zip(idle, o)))
        else:
            # too many: each vehicle takes its nearest free spot
            order = []
            left = spots[:]
            for v in idle:
                s = min(left, key=lambda s: dist(v.pos, s))
                order.append(s)
                left.remove(s)
        targets = {v.id: s for v, s in zip(idle, order)}
        return (targets, float((W * stay).sum() / W.sum()), float(score / W.sum()))

    # MAIN LOOP!!!

    def run(self, calls):
        calls = sorted(calls, key=lambda c: (c.t, c.id))
        if not calls:
            return
        if calls[-1].t >= MIDNIGHT:
            raise ValueError("every call must come in before midnight")
        i = 0 # next call to come in
        t = calls[0].t # start the clock at the first call
        while True:
            # 1. vehicles in motion advance one intersection
            for v in self.fleet:
                if v.state == "to_job":
                    v.pos = step(v.pos, v.job.pos)
                    self.travel += 1
                    if v.pos == v.job.pos:
                        self.start_job(v, t)
                elif v.dest is not None: # idle, heading to base/standby
                    v.pos = step(v.pos, v.dest)
                    self.travel += 1
                    if v.pos == v.dest:
                        self.log(t, v, f"Arrive at {self.place(v.dest)}")
                        v.dest = None

            # 2. calls arriving this minute (all of them before any planning)
            new = False
            while i < len(calls) and calls[i].t == t:
                self.unassigned.append(calls[i])
                self.seen.append(calls[i])
                i += 1
                new = True

            # 3. finish jobs, re-plan, dispatch. Loops because a 0-minute job
            #    started by dispatch ends in this same minute.
            event = new
            changed = False
            while True:
                done = [v for v in self.fleet if v.state == "on_job" and v.finish_at <= t]
                for v in done:
                    self.log(t, v, "Finish job")
                    self.served[v.job.id]["finish"] = t
                    v.state = "idle"
                    v.job = None
                if not (event or done):
                    break
                event = False
                changed = True
                self.plan(t)
                self.commit(t)
            if changed:
                self.reposition(t)

            # 4. from midnight on, stop once every call is served and the whole
            #    fleet is parked at base
            if t >= MIDNIGHT:
                all_home = all(v.state == "idle" and v.dest is None and v.pos == BASE
                               for v in self.fleet)
                if not self.unassigned and all_home:
                    self.frames.append((t, [v.snapshot() for v in self.fleet]))
                    break
                # every minute: send home whoever is idle now (at midnight, or as
                # its last call ends), and turn round a vehicle passing through
                # base on its way to standby (it can't stop there: no such line)
                self.reposition(t)
            self.frames.append((t, [v.snapshot() for v in self.fleet]))
            t += 1


# Entry points

def schedule(n_vehicles, calls, explain=False, **opts):
    # Run a day. Returns (output lines, stats). explain adds "# ..." note
    # lines after each minute's vehicle lines.
    d = Dispatcher(n_vehicles, **opts)
    d.run(calls)

    # sort key: time, then vehicle lines (0) before notes (1), then vehicle, then order
    rows = []
    for t, vid, seq, text in d.events:
        rows.append(((t, 0, vid, seq), f"Vehicle {vid}: {fmt(t)} {text}"))
    if explain:
        for seq, (t, text) in enumerate(d.notes):
            rows.append(((t, 1, 0, seq), f"# {fmt(t)} {text}"))
    rows.sort()
    lines = [line for _, line in rows]

    delays = list(d.delay.values())
    stats = {"calls": len(calls), "total_delay": sum(delays),
             "max_delay": max(delays, default=0), "travel": d.travel}
    return lines, stats


def main():
    # Very very messy but functional argument parser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("input")
    ap.add_argument("--mode", choices=["standby", "base"], default="standby",
                    help="where idle vehicles go (default: standby)")
    ap.add_argument("--tie-window", type=int, default=0,
                    help="accept up to M extra delay minutes for better coverage")
    ap.add_argument("--no-replan", action="store_true",
                    help="baseline: never move a call once it is queued")
    ap.add_argument("--backlog", choices=["plan", "oldest"], default="plan",
                    help="more waiting calls than every split can be tried for: plan them "
                         "(default) or the old rule, oldest first to the soonest vehicle")
    ap.add_argument("--age-weight", type=float, default=AGE_WEIGHT,
                    help=f"when planning a backlog, weight each call by 1 + this x hours "
                         f"waited (default {AGE_WEIGHT:g}; 0 = plain total delay)")
    ap.add_argument("--explain", action="store_true",
                    help="add '# ...' lines saying why each decision was made")
    ap.add_argument("--stats", action="store_true", help="print a summary to stderr")
    a = ap.parse_args()
    try:
        with open(a.input) as f:
            n_vehicles, calls = parse_text(f.read())
    except (OSError, UnicodeDecodeError, InputError) as e:
        sys.exit(f"error: {a.input}: {e}")
    lines, stats = schedule(n_vehicles, calls, explain=a.explain, mode=a.mode,
                            tie_window=a.tie_window, replan=not a.no_replan,
                            backlog=a.backlog, age_weight=a.age_weight)
    print("\n".join(lines))
    if a.stats:
        print(f"calls={stats['calls']} total_delay={stats['total_delay']} "
              f"max_delay={stats['max_delay']} travel={stats['travel']}", file=sys.stderr)


if __name__ == "__main__":
    main()
