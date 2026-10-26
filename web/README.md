# Dispatch replay

A minute-by-minute replay of the online dispatcher in `apps/solution`: standby
parking and return-to-base side by side on one shared clock, with live totals,
the printed schedule and (optionally) the dispatcher's explain notes.

```bash
npm run dev          # http://localhost:3000
```

The replays are static JSON in `public/replays/`, exported by the dispatcher:

```bash
cd ../solution
.venv/bin/python replay.py inputs/*.txt          # writes ../web/public/replays/*.json and index.json
```

Re-export after changing the dispatcher or adding inputs. To look at a day
without adding it to the list, export it anywhere
(`python replay.py my_day.txt --out /tmp/replays`) and use **Open replay JSON**.

Deep links jump to a moment: `/?replay=typical_day&t=09:35`.

Keys: space plays/pauses, ← and → step one minute.

Built with Next.js 16 (see `AGENTS.md`: this version differs from older docs),
React 19 and Tailwind 4, no other dependencies. Code: `app/components/` (the
viewer and one panel per mode) and `app/lib/replay.ts` (data types and helpers).
