"""
compare the existing dispatch policies on the same random inputs.

generate four call patterns and validate every schedule before counting it.
print the average total wait, wait per call, longest wait and travel.

usage: python compare.py [days_per_scenario] [seed]
"""
import random
import sys

from dispatcher import parse_text, schedule
from validate import validate

#compare the existing queue, return-to-base and standby policies.
POLICIES = {
    "greedy queue, return to base": dict(mode="base", replan=False),
    "re-plan, return to base": dict(mode="base"),
    "re-plan, standby": dict(mode="standby"),
    "re-plan, standby, 3-min tie window": dict(mode="standby", tie_window=3),
}


def around(rng, cx, cy, spread):
    """choose a random location near the given point."""
    return round(rng.gauss(cx, spread)), round(rng.gauss(cy, spread))


def make_day(rng, scenario, n_calls=24, n_vehicles=3):
    """generate a day of calls from 08:00 to 20:00 and return its input text."""
    #generate call times and locations for the chosen pattern.
    times = sorted(rng.randint(8 * 60, 20 * 60) for _ in range(n_calls))
    lines = [str(n_vehicles)]
    for t in times:
        if scenario == "uniform":
            x, y = rng.randint(-20, 20), rng.randint(-20, 20)
        elif scenario == "one hotspot":
            x, y = around(rng, 14, -10, 4) if rng.random() < 0.8 else \
                (rng.randint(-20, 20), rng.randint(-20, 20))
        elif scenario == "two hotspots":
            x, y = around(rng, *rng.choice([(14, 10), (-12, -12)]), 4)
        else:  #moving hotspot: morning downtown, evening across town
            x, y = around(rng, *((14, 10) if t < 14 * 60 else (-12, -12)), 4)
        #write each location using the directions expected by the input format.
        parts = []
        if y:
            parts.append(f"{abs(y)} {'N' if y > 0 else 'S'}")
        if x:
            parts.append(f"{abs(x)} {'E' if x > 0 else 'W'}")
        lines.append(f"{t // 60:02d}:{t % 60:02d} {' '.join(parts) or '0 N'} "
                     f"{rng.randint(10, 45)}")
    return "\n".join(lines) + "\n"


def main():
    """run each policy on the same days and print its average results."""
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    rng = random.Random(int(sys.argv[2]) if len(sys.argv) > 2 else 1)
    scenarios = ["uniform", "one hotspot", "two hotspots", "moving hotspot"]
    checked = 0
    for sc in scenarios:
        #give every policy the same generated days for a fair comparison.
        texts = [make_day(rng, sc) for _ in range(days)]
        print(f"\n{sc} ({days} days, 24 calls, 3 vehicles) - averages per day")
        print(f"  {'policy':38} {'total delay':>11} {'per call':>9} {'worst':>6} {'driving':>8}")
        for name, opts in POLICIES.items():
            tot = worst = drive = 0
            for text in texts:
                V, calls = parse_text(text)
                lines, stats = schedule(V, calls, **opts)
                #check the schedule before including its results.
                r = validate(text, "\n".join(lines))
                if not r["ok"] or r["total_delay"] != stats["total_delay"]:
                    print(f"INVALID schedule from '{name}':\n{text}\n" + "\n".join(r["errors"][:5]))
                    sys.exit(1)
                #collect waiting times and travel after the schedule passes.
                checked += 1
                tot += stats["total_delay"]
                worst += stats["max_delay"]
                drive += stats["travel"]
            print(f"  {name:38} {tot / days:11.0f} {tot / days / 24:9.1f} "
                  f"{worst / days:6.0f} {drive / days:8.0f}")
    print(f"\nAll {checked} schedules passed validate.py.")


if __name__ == "__main__":
    main()
