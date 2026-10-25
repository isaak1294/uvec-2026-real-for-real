"""
check the optimizer against every possible plan on small random inputs.

compare the model's proven result with route replay and exhaustive search.
both searches use the existing benchmark simulator for travel and timing.
stop and report the input if the results disagree.

usage: python verify.py [num_instances] [seed]
"""
import random
import sys
from itertools import permutations, combinations_with_replacement

from cpsat_dispatch import simulate, solve_cpsat, greedy_routes


def brute_force(V, calls):
    """try every call order and every way to divide calls between vehicles."""
    ids = [c["id"] for c in calls]
    n, best = len(ids), None
    #try every call order and every split between the vehicles.
    for perm in permutations(ids):
        for cuts in combinations_with_replacement(range(n + 1), V - 1):
            b = (0,) + cuts + (n,)
            routes = [list(perm[b[k]:b[k + 1]]) for k in range(V)]
            #score the complete plan using the existing route simulator.
            tot = simulate(V, calls, routes, verbose=False)
            if best is None or tot < best:
                best = tot
    return best


def random_instance(rng):
    """generate a small fleet and random calls for checking the optimizer."""
    V = rng.randint(1, 3)
    n = rng.randint(2, 6 if V == 3 else 7)
    start = rng.randint(8 * 60, 16 * 60)
    calls = []
    for i in range(n):
        x = rng.randint(-15, 15) if rng.random() > 0.2 else 0
        y = rng.randint(-15, 15) if rng.random() > 0.2 else 0
        calls.append({"id": i, "t": start + rng.randint(0, 60),
                      "pos": (x, y), "dur": rng.randint(5, 45)})
    #put calls in time order and number them in that order.
    calls.sort(key=lambda c: c["t"])
    for i, c in enumerate(calls):
        c["id"] = i
    return V, calls


if __name__ == "__main__":
    #read the number of cases and the seed used to generate them.
    N = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    rng = random.Random(int(sys.argv[2]) if len(sys.argv) > 2 else 0)
    beat = 0
    for k in range(N):
        #compare the optimizer, replay, exhaustive search and greedy baseline.
        V, calls = random_instance(rng)
        routes, obj, status, bound, _ = solve_cpsat(V, calls, 20, workers=4)
        cp = simulate(V, calls, routes, verbose=False)
        bf = brute_force(V, calls)
        gr = simulate(V, calls, greedy_routes(V, calls), verbose=False)
        #require a proven optimum and agreement between the results.
        ok = status == "OPTIMAL" and cp == obj == bf <= gr
        #stop at the first mismatch and print the input needed to reproduce it.
        if not ok:
            print(f"MISMATCH #{k}: V={V} n={len(calls)} status={status} "
                  f"model={obj} sim={cp} brute={bf} greedy={gr}\n  {calls}")
            sys.exit(1)
        beat += gr > bf
    print(f"{N}/{N} instances: CP-SAT proven optimum == exhaustive optimum. "
          f"Greedy was suboptimal on {beat}.")
