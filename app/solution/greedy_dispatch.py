#!/usr/bin/env python3
"""Greedy emergency dispatcher (baseline).

Each call goes to whichever vehicle can reach it earliest: idle at base,
heading back to base (rerouted from wherever it is), or still busy (it goes
straight to the new job when it finishes). Idle vehicles head back to base.

Also home to the shared rules (parsing, travel, return-to-base movement) and
the schedule replay, so cpsat_dispatch.py is scored the exact same way.

Usage: python greedy_dispatch.py input.txt
"""
import sys

BASE = (0, 0)  # (east, north)


# ---------------------------------------------------------------- input / output
def parse_time(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def fmt(t):
    return f"{t // 60 % 24:02d}:{t % 60:02d}"


def parse(path):
    """Input: vehicle count, then one call per line: HH:MM [blocks dir]... duration."""
    with open(path) as f:
        lines = [l.split() for l in f if l.strip()]
    n_vehicles = int(lines[0][0])
    calls = []
    for i, tok in enumerate(lines[1:]):
        t, dur = parse_time(tok[0]), int(tok[-1])
        x = y = 0
        mid = tok[1:-1]
        for k in range(0, len(mid), 2):
            d, c = int(mid[k]), mid[k + 1].upper()
            if c == "N": y += d
            elif c == "S": y -= d
            elif c == "E": x += d
            elif c == "W": x -= d
        calls.append({"id": i, "t": t, "pos": (x, y), "dur": dur})
    return n_vehicles, sorted(calls, key=lambda c: c["t"])


def drive_text(a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    parts = []
    if dy: parts.append(f"{abs(dy)} minutes {'north' if dy > 0 else 'south'}")
    if dx: parts.append(f"{abs(dx)} minutes {'east' if dx > 0 else 'west'}")
    return "Drive " + " and ".join(parts) if parts else "Already on scene"


# ---------------------------------------------------------------- movement
def dist(a, b):
    """Manhattan distance; one block takes one minute."""
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def toward_base(pos, minutes):
    """Position after driving `minutes` toward base (N/S leg first, then E/W)."""
    x, y = pos
    step = min(minutes, abs(y))
    y -= step if y > 0 else -step
    minutes -= step
    step = min(minutes, abs(x))
    x -= step if x > 0 else -step
    return (x, y)


class Vehicle:
    def __init__(self, vid):
        self.id = vid
        self.free_at = 0        # time last job finishes
        self.free_pos = BASE    # where it is at free_at
        self.at_base = True     # not yet sent home from free_pos

    def pos_at(self, t):
        """Where the vehicle is at time t >= free_at (drifting home once idle)."""
        if self.at_base or t <= self.free_at:
            return self.free_pos
        return toward_base(self.free_pos, t - self.free_at)

    def reach(self, c):
        """(dispatch time, start position, on-scene time) if sent to call c next.
        Leaves when the call arrives or when the current job ends, whichever is
        later; from wherever it is at that moment (job site or partway home)."""
        dispatch = max(c["t"], self.free_at)
        start = self.pos_at(dispatch)
        return dispatch, start, dispatch + dist(start, c["pos"])

    def take(self, c, arrive):
        self.free_at, self.free_pos, self.at_base = arrive + c["dur"], c["pos"], False


# ---------------------------------------------------------------- greedy rule
def greedy_routes(n_vehicles, calls):
    """Per-vehicle job orders: each call, in time order, goes to the vehicle
    that can be on scene earliest (ties go to the lowest vehicle id)."""
    fleet = [Vehicle(i) for i in range(n_vehicles)]
    routes = [[] for _ in fleet]
    for c in sorted(calls, key=lambda c: c["t"]):
        arrive, v = min(((v.reach(c)[2], v) for v in fleet), key=lambda p: p[0])
        routes[v.id].append(c["id"])
        v.take(c, arrive)
    return routes


# ---------------------------------------------------------------- replay
def simulate(n_vehicles, calls, routes, verbose=True):
    """Replay per-vehicle job orders (ASAP dispatch). Returns total delay.
    This is the single source of truth for times; both solvers' answers are
    scored by it."""
    byid = {c["id"]: c for c in calls}
    fleet = [Vehicle(i) for i in range(n_vehicles)]
    events, total = [], 0  # events: (time, vehicle, seq, text)

    def log(t, v, text):
        events.append((t, v.id, len(events), text))

    def close_return_leg(v, until):
        """Log the trip home that started at v.free_at, cut off at `until`."""
        if v.at_base or v.free_pos == BASE:
            return
        start, eta = v.free_at, v.free_at + dist(v.free_pos, BASE)
        if until is None or until > start:
            log(start, v, "Start route to base")
            if until is None or eta <= until:
                log(eta, v, "Arrive at base")
            else:
                log(until, v, "Rerouted before reaching base")

    for v, route in zip(fleet, routes):
        for jid in route:
            c = byid[jid]
            dispatch, start_pos, arrive = v.reach(c)
            if verbose:
                if dispatch > v.free_at:
                    close_return_leg(v, dispatch)
                log(dispatch, v, f"Start route to job {c['id']}")
                log(dispatch, v, drive_text(start_pos, c["pos"]))
                log(arrive, v, f"Start job {c['id']}")
                log(arrive + c["dur"], v, f"Finish job {c['id']}")
            total += arrive - c["t"]
            v.take(c, arrive)
        if verbose:
            close_return_leg(v, None)

    if verbose:
        for t, vid, _, text in sorted(events):
            print(f"Vehicle {vid}: {fmt(t)} {text}")
    return total


def solve(n_vehicles, calls):
    total_delay = simulate(n_vehicles, calls, greedy_routes(n_vehicles, calls))
    print(f"\nTotal response delay: {total_delay} min across {len(calls)} calls")
    return total_delay


if __name__ == "__main__":
    solve(*parse(sys.argv[1] if len(sys.argv) > 1 else "input.txt"))
