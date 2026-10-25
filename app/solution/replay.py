"""export dispatcher runs as json for the website.

each input produces one named json file with calls and a run for each mode.
frames store vehicle positions and states for every minute from start to end.
states are p for parked, m for idle movement, d for driving to a job and j for service.
outcomes store each call's vehicle, dispatch, arrival and finish times.
lines contain schedule events, notes explain decisions and stats contain totals.
times are minutes since the first midnight and do not wrap after midnight.
index.json lists every exported replay in the output folder.

usage: python replay.py input.txt [more.txt ...] [--out DIR]
the output folder defaults to ../web/public/replays beside the solution folder.
"""
import argparse
import glob
import json
import os
import sys

from dispatcher import Dispatcher, InputError, parse_text, spot

#export both policies so the website can compare them.
MODES = {"standby": "Standby", "base": "Return to base"}
DEFAULT_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "web", "public", "replays")


def run(n_vehicles, calls, mode):
    """run one policy and collect the data needed to replay it."""
    d = Dispatcher(n_vehicles, mode=mode)
    d.run(calls)
    #check that there is one frame for every minute in the run.
    start = d.frames[0][0] if d.frames else 0
    assert all(t == start + i for i, (t, _) in enumerate(d.frames)), "frames must be every minute"
    #collect vehicle frames, call outcomes, schedule events and totals.
    delays = list(d.delay.values())
    return {
        "label": MODES[mode],
        "start": start,
        "end": d.frames[-1][0] if d.frames else 0,
        "frames": [f for _, f in d.frames],
        "outcomes": [d.served[c.id] for c in calls],
        "lines": [[t, vid, text] for t, vid, _, text in sorted(d.events)],
        "notes": [[t, text] for t, text in d.notes],
        "stats": {"calls": len(calls), "total_delay": sum(delays),
                  "max_delay": max(delays, default=0), "travel": d.travel},
    }


def export(path, out):
    """read an input file and save both modes in one replay json file."""
    with open(path) as f:
        n_vehicles, calls = parse_text(f.read())
    #use the input filename to name the replay and include both modes.
    name = os.path.splitext(os.path.basename(path))[0]
    data = {
        "name": name,
        "vehicles": n_vehicles,
        "calls": [{"id": c.id, "t": c.t, "x": c.pos[0], "y": c.pos[1], "dur": c.dur,
                   "spot": spot(c.pos)} for c in calls],
        "modes": {mode: run(n_vehicles, calls, mode) for mode in MODES},
    }
    #save the replay using compact json.
    with open(os.path.join(out, f"{name}.json"), "w") as f:
        json.dump(data, f, separators=(",", ":"))
    return name


def write_index(out):
    """rebuild the list of replay files and their summary statistics."""
    entries = []
    #include each exported replay and skip the index itself.
    for path in sorted(glob.glob(os.path.join(out, "*.json"))):
        if os.path.basename(path) == "index.json":
            continue
        with open(path) as f:
            d = json.load(f)
        runs = d["modes"].values()
        entries.append({"name": d["name"], "file": os.path.basename(path),
                        "vehicles": d["vehicles"], "calls": len(d["calls"]),
                        "start": min(r["start"] for r in runs),
                        "end": max(r["end"] for r in runs),
                        "stats": {m: r["stats"] for m, r in d["modes"].items()}})
    #replace the index with the current list and summary statistics.
    with open(os.path.join(out, "index.json"), "w") as f:
        json.dump({"replays": entries}, f, indent=1)
    return len(entries)


def main():
    """export the requested inputs and update the website replay index."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--out", default=DEFAULT_OUT)
    a = ap.parse_args()
    #create the output folder and export each requested input.
    os.makedirs(a.out, exist_ok=True)
    for path in a.inputs:
        try:
            print(f"{path} -> {export(path, a.out)}.json")
        except (OSError, InputError) as e:
            sys.exit(f"error: {path}: {e}")
    #refresh the list the website uses to find available replays.
    print(f"index.json lists {write_index(a.out)} replays in {os.path.normpath(a.out)}")


if __name__ == "__main__":
    main()
