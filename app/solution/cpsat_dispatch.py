#!/usr/bin/env python3
"""Optimal emergency dispatcher using OR-Tools CP-SAT.

Same rules as greedy_dispatch.py, so the two are directly comparable:
  - Vehicles start at base (0,0); travel = Manhattan distance, 1 min/block.
  - A vehicle can't leave for a call before the call arrives.
  - After a job, an idle vehicle drives back to base (N/S leg first). If its
    next job comes in on the way, it reroutes from wherever it is.
Objective: minimize total response delay = sum(arrival_on_scene - call_time).

Hindsight benchmark, not a submission: it sees the whole day in advance, which
the "as they come" rule forbids. Under the same return-to-base rules its proven
optimum is a floor for dispatcher.py --mode base (see hindsight_check.py).

Usage: python cpsat_dispatch.py input.txt [time_limit_seconds]
"""
import sys
import time
from ortools.sat.python import cp_model

# simulate and greedy_routes are re-exported for verify.py / hindsight_check.py
from greedy_dispatch import BASE, parse, dist, toward_base, simulate, greedy_routes


def travel_table(src, dst):
    """Travel time src -> dst after idling g minutes (drifting toward base),
    for g = 0 .. minutes to reach base. Past that the vehicle is home."""
    return [dist(toward_base(src, g), dst) for g in range(dist(src, BASE) + 1)]


# ---------------------------------------------------------------- CP-SAT model
def solve_cpsat(n_vehicles, calls, time_limit=30.0, workers=0, hint=True):
    """Returns (routes, objective, status name, lower bound, wall seconds).
    Status OPTIMAL means the objective is proven best; FEASIBLE means the time
    limit hit first, so the true optimum lies between bound and objective."""
    n = len(calls)
    if n == 0:
        return [[] for _ in range(n_vehicles)], 0, "OPTIMAL", 0, 0.0
    # node 0 = base, node k = calls[k-1]
    pos = [BASE] + [c["pos"] for c in calls]
    tcall = [0] + [c["t"] for c in calls]
    dur = [0] + [c["dur"] for c in calls]
    D = max(dist(a, b) for a in pos for b in pos)
    H = max(tcall) + sum(dur) + (n + 1) * D + 1  # safe horizon

    m = cp_model.CpModel()
    s = [None] + [m.NewIntVar(tcall[j], H, f"s{j}") for j in range(1, n + 1)]    # dispatch
    arr = [None] + [m.NewIntVar(tcall[j], H, f"a{j}") for j in range(1, n + 1)]  # on scene
    done = [None] + [arr[j] + dur[j] for j in range(1, n + 1)]

    # x[i, j] = 1 if the vehicle that does i does j next (0 = leaves/returns to base)
    lits = {(i, j): m.NewBoolVar(f"x{i}_{j}")
            for i in range(n + 1) for j in range(n + 1) if i != j}
    for (i, j), lit in lits.items():
        if j == 0:
            continue  # going home: no timing constraint needed
        if i == 0:
            # first job of a route: leaves base the moment the call arrives
            m.Add(s[j] == tcall[j]).OnlyEnforceIf(lit)
            m.Add(arr[j] == s[j] + dist(BASE, pos[j])).OnlyEnforceIf(lit)
            continue
        # i -> j on the same vehicle: dispatch ASAP = max(call_j, done_i).
        # Must be an equality — with ">=", the solver learns to stall a
        # vehicle at base so it never drives home away from the next call.
        ready = m.NewIntVar(0, H, f"m{i}_{j}")
        m.AddMaxEquality(ready, [done[i], tcall[j]])
        m.Add(s[j] == ready).OnlyEnforceIf(lit)
        # idle gap g = s_j - done_i; vehicle drifts toward base for g minutes,
        # saturating once home. travel = dist(position after g, job j).
        table = travel_table(pos[i], pos[j])
        home = len(table) - 1
        raw = m.NewIntVar(-H, H, f"r{i}_{j}")
        m.Add(raw == s[j] - done[i]).OnlyEnforceIf(lit)
        gap = m.NewIntVar(0, home, f"g{i}_{j}")
        m.AddMinEquality(gap, [raw, home])
        travel = m.NewIntVar(min(table), max(table), f"t{i}_{j}")
        m.AddElement(gap, table, travel)
        m.Add(arr[j] == s[j] + travel).OnlyEnforceIf(lit)

    # Redundant cuts (valid for every schedule, they just tighten the LP bound):
    # each call has exactly one predecessor, so its response delay is at least
    # the cheapest travel from whichever predecessor is chosen.
    for j in range(1, n + 1):
        mt = {i: min(travel_table(pos[i], pos[j])) for i in range(n + 1) if i != j}
        m.Add(arr[j] >= tcall[j] + min(mt.values()))
        m.Add(arr[j] - tcall[j] >= sum(mt[i] * lits[i, j] for i in mt))

    m.AddMultipleCircuit([(i, j, lit) for (i, j), lit in lits.items()])  # every call served once
    m.Add(sum(lits[0, j] for j in range(1, n + 1)) <= n_vehicles)          # fleet size
    m.Minimize(sum(arr[j] for j in range(1, n + 1)) - sum(tcall[1:]))

    if hint:  # warm start from greedy
        node = {c["id"]: k + 1 for k, c in enumerate(calls)}  # call id -> node
        used = set()
        for r in greedy_routes(n_vehicles, calls):
            path = [0] + [node[cid] for cid in r] + [0]
            used.update((a, b) for a, b in zip(path, path[1:]) if a != b)
        for arc, lit in lits.items():
            m.AddHint(lit, 1 if arc in used else 0)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_workers = workers  # 0 = all cores
    t0 = time.time()
    st = solver.Solve(m)
    wall = time.time() - t0
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise RuntimeError(f"no solution: {solver.StatusName(st)}")

    # Read routes back: follow chosen arcs out of base until returning to it.
    routes = []
    for j in range(1, n + 1):
        if solver.Value(lits[0, j]):
            r, cur = [], j
            while cur != 0:
                r.append(calls[cur - 1]["id"])
                cur = next(k for k in range(n + 1) if k != cur and solver.Value(lits[cur, k]))
            routes.append(r)
    routes += [[] for _ in range(n_vehicles - len(routes))]
    return (routes, round(solver.ObjectiveValue()), solver.StatusName(st),
            round(solver.BestObjectiveBound()), wall)


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "input.txt"
    limit = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0
    V, calls = parse(path)
    routes, obj, status, bound, wall = solve_cpsat(V, calls, limit)
    sim = simulate(V, calls, routes)
    greedy = simulate(V, calls, greedy_routes(V, calls), verbose=False)
    print(f"\nTotal response delay: {sim} min across {len(calls)} calls")
    print(f"CP-SAT status: {status}  objective={obj}  lower bound={bound}  ({wall:.2f}s)")
    print(f"Greedy baseline: {greedy} min  ->  improvement {greedy - sim} min")
    assert sim == obj, "simulator and model disagree"
