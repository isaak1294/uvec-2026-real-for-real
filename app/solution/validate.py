import re
import sys

#read the vehicle number, hour, minute and action from a schedule line.
LINE = re.compile(r"^Vehicle (\d+): (\d{1,2}):(\d{2}) (.+)$")

#read one or two distances and directions from a driving instruction.
DRIVE = re.compile(
    r"^Drive (\d+) minutes (north|south|east|west)"
    r"(?: and (\d+) minutes (north|south|east|west))?$"
)

#describe how one block in each direction changes (east, north).
UNIT = {
    "north": (0, 1),
    "south": (0, -1),
    "east": (1, 0),
    "west": (-1, 0),
}

def read_input(text):
    '''
    Reads input text and parses it so that it returns numbers of vehicles and a list of their calls. 

    Location is stored as (east, north).
    Times are stored as minutes since midnight.
    Call times must stay in order.
    '''
    #organizes the input text into rows of words
    rows = [r.split() for r in text.splitlines() if r.strip()]
    letters = {"N": (0, 1), "S": (0, -1), "E": (1, 0), "W": (-1, 0)}
    calls = []
    last = -1

    #skip first row since it's vehicle count
    for i, r in enumerate(rows[1:]):
        h, m = r[0].split(":")
        x = y = 0

        #combine distance and direction pairs into one location.
        for k in range(1, len(r) - 1, 2):
            ux, uy = letters[r[k + 1].upper()]
            x, y = x + int(r[k]) * ux, y + int(r[k]) * uy

        #check that this call is not earlier than the previous call.
        t = int(h) * 60 + int(m)
        if t < last:
            raise ValueError(
                f"call {i} at {r[0]} is earlier than the call before it"
            )
        last = t

        #store the calls details so the checker can track its service later.
        calls.append({
            "id": i,
            "t": t,
            "pos": (x, y),
            "dur": int(r[-1]),
            "served": False,
        })

    return int(rows[0][0]), calls

def manhattan(pos1, pos2):
    '''
    Calculates the Manhattan distance between two positions.
    '''
    return abs(pos1[0] - pos2[0]) + abs(pos1[1] - pos2[1])


def vehicle_position(start, dest, minutes):
    """
    find a vehicle's position after driving for the given minutes.

    locations are stored as (east, north).
    move north/south first, then east/west.
    stop at the destination if there is time left over.
    """
    (x, y), (tx, ty) = start, dest

    #spend the available time moving north or south toward the destination.
    s = min(minutes, abs(ty - y))
    y += s if ty > y else -s
    minutes -= s

    #use any remaining time to move east or west.
    s = min(minutes, abs(tx - x))
    x += s if tx > x else -s

    return (x, y)


def validate(input_text, schedule_text):
    """
    check a printed schedule against the input calls.

    track each vehicle's travel and service.
    return any errors and the response delay and travel totals.
    """

    #read the vehicle count and calls from the input text.
    n_vehicles, calls = read_input(input_text)

    #start with no errors, no recorded response delays and no travel.
    errors = []
    delays = []
    travel = 0

    #start every vehicle parked at base, with no active journey.
    veh = [
        {
            "pos": (0, 0),
            "mode": "parked",
            "trip": None,
            "pending": None,
        }
        for _ in range(n_vehicles)
    ]

    #remember the previous schedule time when reading events later.
    last_t = -1

    #read the schedule one line at a time, counting from line 1.
    for ln, raw in enumerate(schedule_text.splitlines(), 1):
        line = raw.strip()

        #skip blank lines and explanation notes.
        if not line or line.startswith("#"):
            continue

        #read the expected format and record an error if it does not match.
        m = LINE.match(line)
        if not m:
            errors.append(f"line {ln}: not in the required format: {line}")
            continue

        #separate the vehicle number, action time and action description.
        vid = int(m[1])
        t = int(m[2]) * 60 + int(m[3])
        msg = m[4].strip()

        #record a problem with the current schedule line.
        def bad(why):
            errors.append(f"line {ln}: {why}  [{line}]")

        #check that the clock time and vehicle number are valid.
        if int(m[2]) > 23 or int(m[3]) > 59:
            bad("not a 24-hour clock time")
            continue

        if not 0 <= vid < n_vehicles:
            bad("no such vehicle")
            continue

        #handle the printed clock wrapping back to zero after midnight.
        while t < last_t:
            t += 24 * 60
        last_t = t

        #select this vehicle's tracking record for the next checks.
        v = veh[vid]

        #check that a route announcement is followed by driving details.
        #a job at the current position can start without a drive line.
        if v["pending"] and not msg.startswith("Drive") and not (
            msg == "Start job" and v["pending"][0] == "job"
        ):
            bad("'Start route' was not followed by a Drive line")
            v["pending"] = None

        #recognize the start of a journey and check its destination type.
        if msg.startswith("Start route to "):
            kind = msg[len("Start route to "):]
            if kind not in ("job", "base", "standby"):
                bad("unknown destination")
                continue

            #a vehicle travelling to a job or working cannot start another route.
            if v["mode"] in ("to_job", "on_job"):
                bad("vehicle is already committed to a job")
                continue

            #find the actual position when a return or standby journey is interrupted.
            if v["mode"] == "idle_trip":
                t0, a, b, _ = v["trip"]
                if t - t0 >= manhattan(a, b):
                    bad("previous trip should already have ended with an Arrive line")

                driven = min(t - t0, manhattan(a, b))
                travel += driven
                v["pos"], v["mode"] = vehicle_position(a, b, driven), "parked"

            #remember the destination type and departure time until driving details arrive.
            v["pending"] = (kind, t)

        #read driving details only when a route was announced at the same time.
        elif msg.startswith("Drive"):
            d = DRIVE.match(msg)
            if not d or not v["pending"] or v["pending"][1] != t:
                bad("Drive line without a matching 'Start route' in the same minute")
                v["pending"] = None
                continue

            #collect the distance and direction for each part of the journey.
            legs = [(int(d[1]), d[2])]
            if d[3]:
                legs.append((int(d[3]), d[4]))

            if len(legs) == 2 and not (
                legs[0][1] in ("north", "south")
                and legs[1][1] in ("east", "west")
            ):
                bad("expected the north/south leg first, then east/west")

            #calculate the destination from the vehicle's current position.
            dx = sum(n * UNIT[w][0] for n, w in legs)
            dy = sum(n * UNIT[w][1] for n, w in legs)
            dest = (v["pos"][0] + dx, v["pos"][1] + dy)
            kind = v["pending"][0]
            v["pending"] = None

            #check that base routes end at base and job routes lead to an open call.
            if kind == "base" and dest != (0, 0):
                bad(f"route 'to base' ends at {dest}, not at base")

            if kind == "job":
                if not any(
                    not c["served"] and c["pos"] == dest and c["t"] <= t
                    for c in calls
                ):
                    bad(f"no open call at {dest} that had arrived by {m[2]}:{m[3]}")
                v["mode"] = "to_job"
            else:
                v["mode"] = "idle_trip"

            #remember when the journey started, its endpoints and its type.
            v["trip"] = (t, v["pos"], dest, kind)

        #check arrival at a job, including jobs already at the current position.
        elif msg == "Start job":
            if v["pending"] and v["pending"][0] == "job":
                v["pending"] = None
                v["mode"], v["trip"] = "to_job", (t, v["pos"], v["pos"], "job")

            if v["mode"] != "to_job":
                bad("'Start job' while not driving to a job")
                continue

            t0, a, b, _ = v["trip"]
            if t != t0 + manhattan(a, b):
                bad(
                    f"arrival should be at minute {t0 + manhattan(a, b)} "
                    f"(left {t0}, {manhattan(a, b)} blocks)"
                )

            travel += manhattan(a, b)
            v["pos"], v["mode"], v["job_start"] = b, "on_job", t

        #match completed service to an unserved call at this location.
        elif msg == "Finish job":
            if v["mode"] != "on_job":
                bad("'Finish job' while not on a job")
                continue

            dur = t - v["job_start"]
            left = v["trip"][0]
            match = [
                c for c in calls
                if not c["served"] and c["pos"] == v["pos"]
                and c["t"] <= left and c["dur"] == dur
            ]

            if not match:
                bad(
                    f"no unserved call at {v['pos']} lasting {dur} min "
                    "that arrived by departure"
                )
            else:
                #match the latest eligible call so earlier departures can match earlier calls.
                c = max(match, key=lambda c: c["t"])
                c["served"] = True
                delays.append(v["job_start"] - c["t"])

            v["mode"] = "parked"

        #check that a return or standby journey ends at the expected place and time.
        elif msg in ("Arrive at base", "Arrive at standby"):
            if v["mode"] != "idle_trip":
                bad("'Arrive' while not on an idle trip")
                continue

            t0, a, b, kind = v["trip"]
            if msg != f"Arrive at {kind}":
                bad(f"trip was 'to {kind}'")
            if t != t0 + manhattan(a, b):
                bad(f"arrival should be at minute {t0 + manhattan(a, b)}")

            travel += manhattan(a, b)
            v["pos"], v["mode"] = b, "parked"

        else:
            bad("unrecognized line")

    #report any calls that were never matched to completed service.
    for c in calls:
        if not c["served"]:
            errors.append(f"call {c['id']} (input line {c['id'] + 2}) was never served")

    #check that every vehicle has finished its work and returned to base.
    for i, v in enumerate(veh):
        if v["mode"] in ("to_job", "on_job"):
            errors.append(f"vehicle {i} ends the day mid-job")
        elif v["mode"] == "idle_trip" or v["pos"] != (0, 0):
            errors.append(f"vehicle {i} ends the day away from base")

    #return whether the schedule passed, its errors and the calculated totals.
    return {
        "ok": not errors,
        "errors": errors,
        "total_delay": sum(delays),
        "max_delay": max(delays, default=0),
        "travel": travel,
    }


if __name__ == "__main__":
    #read the calls from a file and the schedule from a file or piped input.
    with open(sys.argv[1]) as f:
        inp = f.read()
    sched = open(sys.argv[2]).read() if len(sys.argv) > 2 else sys.stdin.read()

    #report input parsing problems with a readable error message.
    try:
        r = validate(inp, sched)
    except (ValueError, KeyError, IndexError) as e:
        sys.exit(f"ERROR could not read the input file {sys.argv[1]}: {e!r}")

    #print each error and the final result, then set the command's exit status.
    for e in r["errors"]:
        print("ERROR", e)
    print(
        ("VALID" if r["ok"] else "INVALID")
        + f"  total_delay={r['total_delay']} max_delay={r['max_delay']} travel={r['travel']}"
    )
    sys.exit(0 if r["ok"] else 1)
