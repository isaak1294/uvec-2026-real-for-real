// Types and helpers for replays exported by apps/solution/replay.py.
// Times are minutes since 00:00 of the first day; x = east, y = north, base = (0, 0).

export type VehicleState = "p" | "m" | "d" | "j"; // parked, moving while idle, driving to job, on job
export type Snapshot = [number, number, VehicleState] | [number, number, VehicleState, number, number];

export interface Call {
  id: number;
  t: number;
  x: number;
  y: number;
  dur: number;
  spot: string;
}

export interface Outcome {
  vehicle: number;
  dispatch: number;
  arrive: number;
  finish: number;
}

export interface Stats {
  calls: number;
  total_delay: number;
  max_delay: number;
  travel: number;
}

export interface ModeRun {
  label: string;
  start: number;
  end: number;
  frames: Snapshot[][];
  outcomes: Outcome[];
  lines: [number, number, string][];
  notes: [number, string][];
  stats: Stats;
}

export interface Replay {
  name: string;
  vehicles: number;
  calls: Call[];
  modes: Record<string, ModeRun>;
}

export interface IndexEntry {
  name: string;
  file: string;
  vehicles: number;
  calls: number;
  start: number;
  end: number;
  stats: Record<string, Stats>;
}

/** Display order: standby first, then base, then anything else. */
export function modeKeys(replay: Replay): string[] {
  const order = ["standby", "base"];
  return Object.keys(replay.modes).sort(
    (a, b) => (order.indexOf(a) + 1 || 99) - (order.indexOf(b) + 1 || 99) || a.localeCompare(b),
  );
}

/** "typical_day" -> "Typical day". */
export function scenarioTitle(name: string): string {
  const s = name.replace(/[_-]+/g, " ").trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** One line on what a mode does with idle vehicles. */
export function modeBlurb(key: string): string {
  if (key === "standby") return "Idle vehicles wait near where calls have come from.";
  if (key === "base") return "Idle vehicles drive back to base.";
  return "";
}

/** "HH:MM" on a 24-hour clock that wraps at midnight. */
export function clock(t: number): string {
  const m = Math.floor(t);
  const hh = Math.floor(m / 60) % 24;
  return `${String(hh).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
}

/** The first minute in [lo, hi] showing clock time "HH:MM" (clamped to the
 *  range), or null if s isn't a clock time. Used for ?t= deep links. */
export function atClock(s: string | null, lo: number, hi: number): number | null {
  const m = s?.match(/^(\d{1,2}):(\d{2})$/);
  if (!m || Number(m[1]) > 23 || Number(m[2]) > 59) return null;
  let t = Number(m[1]) * 60 + Number(m[2]);
  while (t < lo) t += 1440;
  return Math.min(t, hi);
}

/** Days after the first (0 on the first day). */
export function dayOf(t: number): number {
  return Math.floor(Math.floor(t) / 1440);
}

export function timeRange(replay: Replay): [number, number] {
  const runs = Object.values(replay.modes);
  if (!runs.length) return [0, 0];
  return [Math.min(...runs.map((r) => r.start)), Math.max(...runs.map((r) => r.end))];
}

/** Vehicle snapshots at minute t: everyone parked at base before the run starts,
 *  the last frame held after it ends. */
export function frameAt(run: ModeRun, vehicles: number, t: number): Snapshot[] {
  const i = Math.floor(t) - run.start;
  if (i < 0 || !run.frames.length) {
    return Array.from({ length: vehicles }, () => [0, 0, "p"] as Snapshot);
  }
  return run.frames[Math.min(i, run.frames.length - 1)];
}

export interface Point {
  x: number;
  y: number;
}

export interface VehicleView extends Point {
  id: number;
  state: VehicleState;
  dest: Point | null;
}

/** Vehicle positions at fractional time t, moving smoothly between minute frames
 *  (each minute moves at most one block in a straight line, so this is exact). */
export function vehiclesAt(run: ModeRun, vehicles: number, t: number): VehicleView[] {
  const a = frameAt(run, vehicles, t);
  const b = frameAt(run, vehicles, Math.floor(t) + 1);
  const f = t - Math.floor(t);
  return a.map((s, id) => {
    const n = b[id] ?? s;
    return {
      id,
      x: s[0] + (n[0] - s[0]) * f,
      y: s[1] + (n[1] - s[1]) * f,
      state: s[2],
      dest: s.length === 5 ? { x: s[3], y: s[4] } : null,
    };
  });
}

export type CallState = "hidden" | "waiting" | "enroute" | "serving" | "done";

export function callState(call: Call, o: Outcome | undefined, t: number): CallState {
  const m = Math.floor(t);
  if (m < call.t) return "hidden";
  if (!o || m < o.dispatch) return "waiting";
  if (m < o.arrive) return "enroute";
  if (m < o.finish) return "serving";
  return "done";
}

export interface LiveStats {
  onScene: number;
  waiting: number;
  totalDelay: number;
  worstDelay: number;
}

export function liveStats(calls: Call[], run: ModeRun, t: number): LiveStats {
  const m = Math.floor(t);
  const s: LiveStats = { onScene: 0, waiting: 0, totalDelay: 0, worstDelay: 0 };
  calls.forEach((c, i) => {
    const o = run.outcomes[i];
    if (o && o.arrive <= m) {
      s.onScene += 1;
      s.totalDelay += o.arrive - c.t;
      s.worstDelay = Math.max(s.worstDelay, o.arrive - c.t);
    } else if (c.t <= m && (!o || o.dispatch > m)) {
      s.waiting += 1;
    }
  });
  return s;
}

export interface VehicleStatus {
  id: number;
  doing: string;
  detail: string;
}

/** What each vehicle is doing at minute t, in plain words. */
export function fleetStatus(calls: Call[], run: ModeRun, vehicles: VehicleView[], t: number): VehicleStatus[] {
  const m = Math.floor(t);
  return vehicles.map((v) => {
    const i = run.outcomes.findIndex((o) => o && o.vehicle === v.id && o.dispatch <= m && m < o.finish);
    if (i >= 0) {
      const c = calls[i];
      const o = run.outcomes[i];
      return m < o.arrive
        ? { id: v.id, doing: `Driving to call ${c.id} (${c.spot})`, detail: `on scene ${clock(o.arrive)}` }
        : { id: v.id, doing: `On scene at call ${c.id} (${c.spot})`, detail: `free ${clock(o.finish)}` };
    }
    const atBase = v.dest ? v.dest.x === 0 && v.dest.y === 0 : Math.round(v.x) === 0 && Math.round(v.y) === 0;
    const where = atBase ? "base" : "a standby spot";
    return v.state === "m"
      ? { id: v.id, doing: `Heading to ${where}`, detail: "idle" }
      : { id: v.id, doing: `Parked at ${where}`, detail: "idle" };
  });
}

/** Calls that have come in by minute t with no vehicle on the way yet. */
export function waitingCalls(calls: Call[], run: ModeRun, t: number): Call[] {
  const m = Math.floor(t);
  return calls.filter((c, i) => {
    const o = run.outcomes[i];
    return c.t <= m && (!o || o.dispatch > m);
  });
}

/** Number of [time, ...] entries (sorted by time) at or before minute t. */
export function countUpTo<T extends [number, ...unknown[]]>(rows: T[], t: number): number {
  const m = Math.floor(t);
  let lo = 0;
  let hi = rows.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (rows[mid][0] <= m) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

export interface Bounds {
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
}

/** Bounds of base, every call and every recorded position in every mode. */
export function boundsOf(replay: Replay): Bounds {
  const b: Bounds = { minX: 0, maxX: 0, minY: 0, maxY: 0 };
  const add = (x: number, y: number) => {
    b.minX = Math.min(b.minX, x);
    b.maxX = Math.max(b.maxX, x);
    b.minY = Math.min(b.minY, y);
    b.maxY = Math.max(b.maxY, y);
  };
  replay.calls.forEach((c) => add(c.x, c.y));
  Object.values(replay.modes).forEach((r) =>
    r.frames.forEach((f) =>
      f.forEach((s) => {
        add(s[0], s[1]);
        if (s.length === 5) add(s[3], s[4]);
      }),
    ),
  );
  return b;
}

/** Light structural check for a replay JSON a user opens from disk. */
export function asReplay(data: unknown): Replay {
  const r = data as Replay;
  if (
    !r ||
    typeof r !== "object" ||
    !Array.isArray(r.calls) ||
    typeof r.vehicles !== "number" ||
    !r.modes ||
    typeof r.modes !== "object" ||
    !Object.values(r.modes).every(
      (m) => m && Array.isArray(m.frames) && Array.isArray(m.outcomes) && Array.isArray(m.lines),
    )
  ) {
    throw new Error("not a replay file from replay.py (expected vehicles, calls and modes)");
  }
  return r;
}

/** Distinct vehicle colors that read on light and dark backgrounds
 *  (no reds: red marks a call waiting for a vehicle). */
export const VEHICLE_COLORS = [
  "#2563eb",
  "#c026d3",
  "#0d9488",
  "#d97706",
  "#7c3aed",
  "#0891b2",
  "#65a30d",
  "#4f46e5",
  "#db2777",
  "#059669",
];

export function vehicleColor(id: number): string {
  return VEHICLE_COLORS[id % VEHICLE_COLORS.length];
}
