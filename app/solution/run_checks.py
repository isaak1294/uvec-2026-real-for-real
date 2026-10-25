"""
run the existing checks for the scheduling solution.

check difficult cases, bad inputs, sample days and backlog planners.
run the hindsight, policy comparison and optimizer verification scripts.
print the results and exit with a nonzero status if any group fails.

the dispatcher and the other checking scripts must be in this folder.
use --full to run more random cases.
"""
import glob
import os
import random
import subprocess
import sys
import time
from itertools import permutations, product

from dispatcher import (Call, InputError, dp_plan, local_search, parse_text, queue_cost,
                        schedule)
from validate import validate

HERE = os.path.dirname(os.path.abspath(__file__))


def has(*wanted):
    """check that the required lines appear in the schedule in this order."""
    def check(lines):
        i = 0
        for line in lines:
            if i < len(wanted) and line == wanted[i]:
                i += 1
        return None if i == len(wanted) else f"missing (in order): {wanted[i]}"
    return check


def lacks(text):
    """check that the schedule does not contain the given text."""
    def check(lines):
        return f"should not contain: {text}" if any(text in l for l in lines) else None
    return check


#case file (mode, expected total delay or None, checks)
CASES = {
    "call_at_finish.txt": ("standby", 8, [has(
        #the job ends the same minute a call comes in: it leaves from the scene
        "Vehicle 0: 09:15 Finish job",
        "Vehicle 0: 09:15 Start route to job",
        "Vehicle 0: 09:15 Drive 3 minutes east",
        "Vehicle 0: 09:18 Start job")]),
    "burst.txt": ("standby", 243, [has(
        #9 calls at once, more than every split is tried for: the backlog
        #planner runs. With every call known up front, online = hindsight, and
        #243 is CP-SAT's proven optimum (oldest first gives 279)
        "# 09:00 9 waiting calls: best split and order among 3 vehicles found exactly "
        "by dynamic programming (a call counts 1 + 1 x hours it has waited): "
        "total wait 243 min (oldest first: 279)")]),
    "zero_length_job.txt": ("standby", 6, [has(
        #a 0-minute job ends on arrival; the next call is at the same spot
        "Vehicle 0: 10:04 Start job",
        "Vehicle 0: 10:04 Finish job",
        "Vehicle 0: 10:04 Start route to job",
        "Vehicle 0: 10:04 Start job")]),
    "call_at_base.txt": ("standby", 5, [has(
        #a call at base ("0 N"): no Drive line, the job starts at once
        "Vehicle 0: 08:00 Start route to job",
        "Vehicle 0: 08:00 Start job")]),
    "past_midnight.txt": ("standby", 45, [has(
        #a job runs past midnight (clocks wrap), the 23:50 call is served after
        #it, and then, past midnight, the vehicle goes straight home
        "Vehicle 0: 00:10 Finish job",
        "Vehicle 0: 00:10 Drive 15 minutes south",
        "Vehicle 0: 00:25 Start job",
        "Vehicle 0: 00:35 Finish job",
        "Vehicle 0: 00:35 Start route to base",
        "Vehicle 0: 00:40 Arrive at base")]),
    "standby_until_midnight.txt": ("standby", 30, [has(
        #four calls at one spot pull standby there (the 4th is reached in 0 min);
        #nothing says the 13:00 call is the last, so it waits there until midnight
        "Vehicle 0: 13:00 Start job",
        "Vehicle 0: 13:10 Finish job",
        "Vehicle 0: 00:00 Start route to base",
        "Vehicle 0: 00:00 Drive 10 minutes south",
        "Vehicle 0: 00:10 Arrive at base"),
        lacks("Vehicle 0: 13:10 Start route to base")]),
    "same_spot.txt": ("standby", None, []),  #two calls, one spot: matched correctly
    "interrupted_return.txt": ("base", 18, [has(
        #heading home when a call comes in: it turns off from where it is
        "Vehicle 0: 09:15 Start route to base",
        "Vehicle 0: 09:20 Start route to job",
        "Vehicle 0: 09:20 Drive 5 minutes south and 3 minutes east"),
        lacks("Vehicle 0: 09:20 Arrive at base")]),
    "same_minute_order.txt": ("standby", 26, [has(
        #two calls in one minute are planned together: the near one first,
        #although the far one is listed first
        "Vehicle 0: 09:00 Drive 1 minutes north",
        "Vehicle 0: 09:06 Drive 19 minutes north")]),
}

#bad input -> words the error must contain (always with the line number)
BAD_INPUTS = {
    "": "the input is empty",
    "0\n10:00 5 N 10\n": "line 1: expected the number of vehicles",
    "two\n10:00 5 N 10\n": "line 1: expected the number of vehicles",
    "2\n25:00 5 N 10\n": "line 2: expected a 24-hour time",
    "2\n10:00 5 X 10\n": "line 2: expected a whole number and N, S, E or W, got '5 X'",
    "2\n10:00 -5 N 10\n": "line 2: expected a whole number and N, S, E or W",
    "2\n10:00 5 N 20 W\n": "line 2: expected the job length in whole minutes last",
    "2\n10:00 5 N 2.5\n": "line 2: expected the job length in whole minutes last",
    "2\n10:00 5 N 3 S 10\n": "line 2: two north/south distances",
    "2\n10:00 5 N 20 W 3 E 10\n": "line 2: expected at most two distances",
    "2\n\n10:00 5 N 10\n10:30 5 N\n": "line 4: expected",
    "1\n23:50 10 N 30\n00:10 5 S 10\n": "line 3: 00:10 is earlier than the call before it (23:50)",
}


def check_cases():
    """validate the difficult cases and their expected results."""
    failures = []
    for name, (mode, total, checks) in CASES.items():
        with open(os.path.join(HERE, "tests", name)) as f:
            text = f.read()
        V, calls = parse_text(text)
        for m in ("standby", "base"):
            lines, stats = schedule(V, calls, explain=True, mode=m)
            r = validate(text, "\n".join(lines))
            if not r["ok"] or r["total_delay"] != stats["total_delay"]:
                failures.append(f"{name} ({m}): invalid: {r['errors'][:2]}")
            if m != mode:
                continue
            if total is not None and stats["total_delay"] != total:
                failures.append(f"{name}: total delay {stats['total_delay']}, expected {total}")
            failures += [f"{name}: {e}" for e in (c(lines) for c in checks) if e]
    return failures, f"{len(CASES)} cases"


def check_bad_inputs():
    """check that bad inputs produce useful errors naming the line."""
    failures = []
    for text, words in BAD_INPUTS.items():
        try:
            parse_text(text)
            failures.append(f"accepted {text!r}")
        except InputError as e:
            if words not in str(e):
                failures.append(f"{text!r}: error {str(e)!r} lacks {words!r}")
        except Exception as e:  #anything but InputError is a crash
            failures.append(f"{text!r}: crashed with {e!r}")
    return failures, f"{len(BAD_INPUTS)} bad inputs"


def check_samples():
    """validate each sample input in both scheduling modes."""
    failures, paths = [], sorted(glob.glob(os.path.join(HERE, "inputs", "*.txt")))
    for path in paths:
        with open(path) as f:
            text = f.read()
        V, calls = parse_text(text)
        for mode in ("standby", "base"):
            lines, stats = schedule(V, calls, mode=mode)
            r = validate(text, "\n".join(lines))
            if not r["ok"] or r["total_delay"] != stats["total_delay"]:
                failures.append(f"{os.path.basename(path)} ({mode}): {r['errors'][:2]}")
    return failures, f"{len(paths)} inputs x 2 modes"


def check_backlog_planner(n):
    """compare small backlog plans with every possible assignment and order."""
    failures, rng = [], random.Random(7)
    for trial in range(n):
        V, k, now = rng.randint(1, 4), rng.randint(1, 6), 600
        pending = [Call(i, now - rng.randint(0, 120), (rng.randint(-15, 15), rng.randint(-15, 15)),
                        rng.choice([0, 5, 10, 20, 40])) for i in range(k)]
        starts = [(now + rng.choice([0, rng.randint(1, 50)]),
                   (rng.randint(-15, 15), rng.randint(-15, 15))) for _ in range(V)]
        w = {c.id: 1 + rng.choice([0, 1, 2]) * (now - c.t) / 60 for c in pending}
        best = min(sum(min(queue_cost(*starts[v], list(order), w) for order in permutations(
            [pending[i] for i in range(k) if split[i] == v])) for v in range(V))
            for split in product(range(V), repeat=k))
        orders = dp_plan(starts, pending, w)
        got = sum(queue_cost(*starts[v], [pending[i] for i in orders[v]], w) for v in range(V))
        if sorted(i for o in orders for i in o) != list(range(k)) or abs(got - best) > 1e-6:
            failures.append(f"backlog {trial}: dynamic program {got}, every split {best}")
        queues = local_search(starts, pending, w)
        searched = sum(queue_cost(*starts[v], queues[v], w) for v in range(V))
        if sorted(c.id for q in queues for c in q) != list(range(k)) or searched < best - 1e-6:
            failures.append(f"backlog {trial}: local search plan invalid")
    return failures, f"{n} random backlogs"


LARGE_DAYS = ["backlog_24.txt", "overload_day.txt", "big_city_day.txt"]


def check_large_days():
    """check large days and compare their delays with the oldest-first rule."""
    failures, notes = [], []
    for name in LARGE_DAYS:
        with open(os.path.join(HERE, "tests", name)) as f:
            text = f.read()
        V, calls = parse_text(text)
        for mode in ("standby", "base"):
            t0 = time.time()
            lines, new = schedule(V, calls, mode=mode)
            secs = time.time() - t0
            _, old = schedule(V, calls, mode=mode, backlog="oldest")
            r = validate(text, "\n".join(lines))
            if not r["ok"] or r["total_delay"] != new["total_delay"]:
                failures.append(f"{name} ({mode}): invalid: {r['errors'][:2]}")
            for key in ("total_delay", "max_delay"):
                if new[key] > old[key]:
                    failures.append(f"{name} ({mode}): {key} {new[key]}, oldest first {old[key]}")
            if mode == "standby":
                notes.append(f"{name.split('.')[0]} {old['total_delay']}->{new['total_delay']} "
                             f"({secs:.0f}s)")
    return failures, f"{len(LARGE_DAYS)} days x 2 modes, delay vs oldest first: " + ", ".join(notes)


def script(*args):
    """prepare a check that runs another script and captures its result."""
    def run():
        p = subprocess.run([sys.executable, *args], cwd=HERE, capture_output=True, text=True)
        out = (p.stdout.strip().splitlines() or [""])[-1]
        return ([] if p.returncode == 0 else [(p.stdout + p.stderr).strip()[-1500:]]), out
    return run


def main():
    """run the check groups and print the final pass or fail count."""
    full = "--full" in sys.argv
    checks = [
        ("tricky cases", check_cases),
        ("bad input errors", check_bad_inputs),
        ("sample inputs", check_samples),
        ("backlog planner", lambda: check_backlog_planner(300 if full else 100)),
        ("large days", check_large_days),
        ("hindsight_check.py", script("hindsight_check.py", "400" if full else "100", "11")),
        ("compare.py", script("compare.py", "15" if full else "4", "1")),
        ("verify.py", script("verify.py", "200" if full else "40", "0")),
    ]
    failed = 0
    for name, check in checks:
        t0 = time.time()
        failures, detail = check()
        status = "FAIL" if failures else "PASS"
        print(f"{status}  {name:20} {detail}  ({time.time() - t0:.0f}s)")
        for f in failures:
            print("      " + f.replace("\n", "\n      "))
        failed += bool(failures)
    print(f"\n{len(checks) - failed}/{len(checks)} checks passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
