"""
compare the online dispatcher with benchmarks that know the whole day.

check schedules, baseline totals and the proven return-to-base optimum.
check that future calls do not change earlier dispatch decisions.
the return-to-base optimum only bounds base mode, since standby may park elsewhere.

usage: python hindsight_check.py [days] [seed]
       python hindsight_check.py inputs/*.txt
"""
import random
import sys

from cpsat_dispatch import simulate, solve_cpsat, greedy_routes
from dispatcher import Call, Dispatcher, parse_text, schedule
from validate import validate

#check the baseline, replanning and standby policies.
POLICIES = [dict(mode="base", replan=False), dict(mode="base"),
            dict(mode="standby"), dict(mode="standby", tie_window=3)]


def random_day(rng):
    """generate a random day with calls in time order before midnight."""
    V, n = rng.randint(1, 4), rng.randint(1, 7 if rng.random() < 0.6 else 25)
    start, rows = rng.randint(6 * 60, 22 * 60 + 29), []  #calls by 23:59
    for _ in range(n):
        x = rng.randint(-15, 15) if rng.random() > 0.2 else 0
        y = rng.randint(-15, 15) if rng.random() > 0.2 else 0
        rows.append((start + rng.randint(0, 90), (x, y), rng.choice([0, 5, 10, 20, 30, 45])))
    return V, sorted(rows, key=lambda r: r[0])


def text_of(V, rows):
    """turn generated call rows into the expected input text."""
    out = [str(V)]
    for t, (x, y), d in rows:
        p = ([f"{abs(y)} {'N' if y > 0 else 'S'}"] if y else []) + \
            ([f"{abs(x)} {'E' if x > 0 else 'W'}"] if x else [])
        out.append(f"{t // 60:02d}:{t % 60:02d} {' '.join(p) or '0 N'} {d}")
    return "\n".join(out) + "\n"


def calls_of(rows):
    """turn generated rows into dispatcher call objects."""
    return [Call(i, t, p, d) for i, (t, p, d) in enumerate(rows)]


def dicts_of(calls):
    """convert call objects into the dictionaries used by the benchmarks."""
    return [{"id": c.id, "t": c.t, "pos": c.pos, "dur": c.dur} for c in calls]


def events(V, rows, opts):
    """run one policy and return its recorded vehicle events."""
    d = Dispatcher(V, **opts)
    d.run(calls_of(rows))
    return d.events


def random_days(n_days, seed):
    """check schedules, benchmark totals and decisions on random days."""
    rng = random.Random(seed)
    schedules = optimal = online = 0
    gap = 0.0
    for k in range(n_days):
        V, rows = random_day(rng)
        text, calls = text_of(V, rows), calls_of(rows)
        totals = []
        #validate every policy and compare its reported total with the checker.
        for opts in POLICIES:
            lines, st = schedule(V, calls_of(rows), **opts)
            r = validate(text, "\n".join(lines))
            if not r["ok"] or r["total_delay"] != st["total_delay"]:
                return False, f"day {k}: invalid schedule from {opts}\n{text}" + "\n".join(r["errors"][:5])
            totals.append(st["total_delay"])
            schedules += 1

        #the baseline must match greedy under the same return-to-base rules.
        greedy = simulate(V, dicts_of(calls), greedy_routes(V, dicts_of(calls)), verbose=False)
        if greedy != totals[0]:
            return False, f"day {k}: hindsight greedy {greedy} != --no-replan {totals[0]}\n{text}"

        #use only a proven optimum when checking the lower bound for base mode.
        if len(rows) <= 7:
            _, opt, status, _, _ = solve_cpsat(V, dicts_of(calls), 20, workers=4)
            if status == "OPTIMAL":
                if totals[1] < opt:
                    return False, f"day {k}: base mode {totals[1]} beats the optimum {opt}\n{text}"
                optimal += 1
                gap += (totals[1] - opt) / max(opt, 1)

        #remove future calls and compare all decisions made before the cutoff.
        if len(rows) >= 3:
            T = rows[rng.randint(1, len(rows) - 1)][0]
            kept = [r for r in rows if r[0] < T]
            if kept:
                for opts in POLICIES:
                    short = events(V, kept, opts)
                    before = lambda ev: sorted((t, v, s) for t, v, _, s in ev if t < T)
                    if before(events(V, rows, opts)) != before(short):
                        return False, f"day {k}: {opts} acted on calls from {T} early\n{text}"
                online += 1

    return True, (f"{n_days} days OK: {schedules} schedules valid; --no-replan matched the "
                  f"hindsight greedy every day; base mode never beat the proven optimum "
                  f"({optimal} days, avg {100 * gap / max(optimal, 1):.1f}% above it); "
                  f"online on {online} cut days.")


def compare_files(paths):
    """print online and hindsight totals for the supplied input files."""
    print(f"{'input':24} {'base':>7} {'standby':>8} {'hindsight opt':>14}  status")
    for path in paths:
        with open(path) as f:
            text = f.read()
        V, calls = parse_text(text)
        got = []
        for mode in ("base", "standby"):
            lines, st = schedule(V, calls, mode=mode)
            if not validate(text, "\n".join(lines))["ok"]:
                sys.exit(f"{path}: invalid {mode} schedule")
            got.append(st["total_delay"])
        _, opt, status, bound, _ = solve_cpsat(V, dicts_of(calls), 30)
        #label the result as proven only when the optimizer proved it.
        note = "proven" if status == "OPTIMAL" else f"best found, floor {bound}"
        print(f"{path.split('/')[-1]:24} {got[0]:7} {got[1]:8} {opt:14}  {note}")


if __name__ == "__main__":
    #accept input file paths or a random-day count and seed.
    if len(sys.argv) > 1 and sys.argv[1].endswith(".txt"):
        compare_files(sys.argv[1:])
    else:
        ok, msg = random_days(int(sys.argv[1]) if len(sys.argv) > 1 else 200,
                              int(sys.argv[2]) if len(sys.argv) > 2 else 0)
        print(msg)
        sys.exit(0 if ok else 1)
