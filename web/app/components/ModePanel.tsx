"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  type Bounds,
  type ModeRun,
  type Replay,
  type VehicleView,
  callState,
  clock,
  countUpTo,
  fleetStatus,
  liveStats,
  modeBlurb,
  vehicleColor,
  vehiclesAt,
  waitingCalls,
} from "../lib/replay";

const LOG_LIMIT = 200;
const WAITING_LIMIT = 3; // waiting calls listed under "Right now" (oldest first)

interface Props {
  modeKey: string;
  replay: Replay;
  run: ModeRun;
  t: number;
  bounds: Bounds;
}

/** One dispatch mode: live numbers, map, what each vehicle is doing, and the schedule. */
export default function ModePanel({ modeKey, replay, run, t, bounds }: Props) {
  const stats = liveStats(replay.calls, run, t);
  const vehicles = vehiclesAt(run, replay.vehicles, t);
  const status = fleetStatus(replay.calls, run, vehicles, t);
  const waiting = waitingCalls(replay.calls, run, t);
  const m = Math.floor(t);

  return (
    <section className="flex min-w-0 flex-col gap-4 rounded-2xl border border-line bg-card p-4 sm:p-5">
      <header className="flex flex-col gap-0.5">
        <h2 className="text-lg font-semibold">{run.label}</h2>
        {modeBlurb(modeKey) && <p className="text-sm text-muted">{modeBlurb(modeKey)}</p>}
      </header>

      <dl className="grid grid-cols-3 gap-2">
        <Stat label="Helped" value={`${stats.onScene}`} unit={`of ${replay.calls.length}`} />
        <Stat label="Waiting now" value={`${stats.waiting}`} warn={stats.waiting > 0} />
        <Stat label="Total wait so far" value={`${stats.totalDelay}`} unit="min" />
      </dl>

      <GridMap replay={replay} run={run} t={t} bounds={bounds} vehicles={vehicles} />

      <div className="flex flex-col gap-2">
        <h3 className="text-sm font-semibold">Right now</h3>
        <ul className="flex flex-col divide-y divide-line rounded-xl border border-line">
          {waiting.slice(0, WAITING_LIMIT).map((c) => (
            <li key={`w${c.id}`} className="flex items-center gap-3 px-3 py-2.5 text-sm">
              <span aria-hidden className="size-3 shrink-0 rounded-sm bg-waiting" />
              <span className="min-w-0 flex-1">
                Call {c.id} ({c.spot}) is waiting for a vehicle
              </span>
              <span className="shrink-0 font-mono text-xs tabular-nums text-waiting">{m - c.t} min</span>
            </li>
          ))}
          {waiting.length > WAITING_LIMIT && (
            <li className="px-3 py-2 text-xs text-waiting">
              +{waiting.length - WAITING_LIMIT} more calls waiting
            </li>
          )}
          {status.map((s) => (
            <li key={s.id} className="flex items-center gap-3 px-3 py-2.5 text-sm">
              <VehicleDot id={s.id} />
              <span className="min-w-0 flex-1">{s.doing}</span>
              <span className="shrink-0 font-mono text-xs tabular-nums text-muted">{s.detail}</span>
            </li>
          ))}
        </ul>
      </div>

      <Log run={run} t={t} />
    </section>
  );
}

function Stat({ label, value, unit, warn = false }: { label: string; value: string; unit?: string; warn?: boolean }) {
  return (
    <div className="flex flex-col gap-0.5 rounded-xl bg-background px-3 py-2.5">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="flex items-baseline gap-1">
        <span className={`font-mono text-xl font-semibold tabular-nums ${warn ? "text-waiting" : ""}`}>{value}</span>
        {unit && <span className="text-xs text-muted">{unit}</span>}
      </dd>
    </div>
  );
}

function VehicleDot({ id }: { id: number }) {
  return (
    <span
      aria-label={`Vehicle ${id}`}
      className="flex size-6 shrink-0 items-center justify-center rounded-full font-mono text-xs font-bold text-white"
      style={{ background: vehicleColor(id) }}
    >
      {id}
    </span>
  );
}

function GridMap({ replay, run, t, bounds, vehicles }: Omit<Props, "modeKey"> & { vehicles: VehicleView[] }) {
  const pad = 2;
  let { minX, maxX, minY, maxY } = bounds;
  const grow = (lo: number, hi: number) => {
    const missing = Math.max(0, 10 - (hi - lo));
    return [lo - Math.ceil(missing / 2), hi + Math.floor(missing / 2)];
  };
  [minX, maxX] = grow(minX, maxX);
  [minY, maxY] = grow(minY, maxY);
  const w = maxX - minX + 2 * pad;
  const h = maxY - minY + 2 * pad;
  const span = Math.max(w, h);
  const u = span / 40; // marker size unit
  const step = span <= 60 ? 5 : span <= 160 ? 10 : 20;

  const gridX: number[] = [];
  for (let x = Math.ceil((minX - pad) / step) * step; x <= maxX + pad; x += step) gridX.push(x);
  const gridY: number[] = [];
  for (let y = Math.ceil((minY - pad) / step) * step; y <= maxY + pad; y += step) gridY.push(y);

  // vehicles sharing a spot (e.g. parked at base) fan out around it so none is hidden
  const together = new Map<string, number[]>();
  vehicles.forEach((v) => {
    const k = `${Math.round(v.x * 4)},${Math.round(v.y * 4)}`;
    together.set(k, [...(together.get(k) ?? []), v.id]);
  });
  const offset = new Map<number, [number, number]>();
  together.forEach((ids) => {
    if (ids.length < 2) return;
    ids.forEach((id, i) => {
      const a = (2 * Math.PI * i) / ids.length - Math.PI / 2;
      offset.set(id, [Math.cos(a) * 1.3 * u, Math.sin(a) * 1.3 * u]);
    });
  });
  const fleetSummary = vehicles
    .map((v) => `vehicle ${v.id} at ${Math.round(v.x)} east, ${Math.round(v.y)} north`)
    .join("; ");

  return (
    <svg
      viewBox={`${minX - pad} ${-(maxY + pad)} ${w} ${h}`}
      className="h-auto max-h-[60vh] w-full rounded-xl border border-line bg-map"
      role="img"
      aria-label={`${run.label} map at ${clock(t)}: ${fleetSummary}`}
    >
      <g style={{ stroke: "var(--grid)" }}>
        {gridX.map((x) => (
          <line key={`x${x}`} x1={x} x2={x} y1={-(maxY + pad)} y2={-(minY - pad)} strokeWidth={1} vectorEffect="non-scaling-stroke" />
        ))}
        {gridY.map((y) => (
          <line key={`y${y}`} y1={-y} y2={-y} x1={minX - pad} x2={maxX + pad} strokeWidth={1} vectorEffect="non-scaling-stroke" />
        ))}
      </g>
      <g style={{ stroke: "var(--axis)" }}>
        <line x1={0} x2={0} y1={-(maxY + pad)} y2={-(minY - pad)} strokeWidth={1} vectorEffect="non-scaling-stroke" />
        <line y1={0} y2={0} x1={minX - pad} x2={maxX + pad} strokeWidth={1} vectorEffect="non-scaling-stroke" />
      </g>
      <text x={maxX + pad - 0.8 * u} y={-(maxY + pad) + 1.6 * u} fontSize={1.1 * u} textAnchor="end" style={{ fill: "var(--muted)" }}>
        N ↑
      </text>

      {/* base */}
      <rect
        x={-1.1 * u}
        y={-1.1 * u}
        width={2.2 * u}
        height={2.2 * u}
        rx={0.3 * u}
        style={{ fill: "var(--card)", stroke: "var(--foreground)" }}
        strokeWidth={2}
        vectorEffect="non-scaling-stroke"
      />

      {/* routes: solid to a job, dashed while idle (to standby or base) */}
      {vehicles.map((v) =>
        v.dest ? (
          <polyline
            key={`r${v.id}`}
            points={`${v.x},${-v.y} ${v.x},${-v.dest.y} ${v.dest.x},${-v.dest.y}`}
            fill="none"
            stroke={vehicleColor(v.id)}
            strokeWidth={2.5}
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeDasharray={v.state === "m" ? "4 5" : undefined}
            strokeOpacity={0.7}
            vectorEffect="non-scaling-stroke"
          />
        ) : null,
      )}

      {/* calls */}
      {replay.calls.map((c, i) => {
        const o = run.outcomes[i];
        const state = callState(c, o, t);
        if (state === "hidden") return null;
        const color = o ? vehicleColor(o.vehicle) : "var(--muted)";
        const s = state === "done" ? 0.5 * u : 0.75 * u;
        const waited = o && o.arrive <= Math.floor(t) ? `, on scene after ${o.arrive - c.t} min` : "";
        return (
          <g key={`c${c.id}`} transform={`translate(${c.x} ${-c.y})`}>
            <title>{`Call ${c.id} at ${c.spot}, came in ${clock(c.t)}, ${c.dur} min job${waited}`}</title>
            {state === "waiting" && (
              <circle r={1.9 * u} className="animate-pulse" style={{ fill: "var(--waiting)" }} opacity={0.18} />
            )}
            <rect
              x={-s}
              y={-s}
              width={2 * s}
              height={2 * s}
              rx={0.25 * u}
              style={{
                fill:
                  state === "waiting"
                    ? "var(--waiting)"
                    : state === "serving"
                      ? color
                      : state === "done"
                        ? "var(--done)"
                        : "var(--card)",
                stroke: state === "enroute" ? color : "none",
              }}
              strokeWidth={2.5}
              vectorEffect="non-scaling-stroke"
            />
          </g>
        );
      })}

      {/* vehicles */}
      {vehicles.map((v) => {
        const [dx, dy] = offset.get(v.id) ?? [0, 0];
        return (
          <g key={`v${v.id}`} transform={`translate(${v.x + dx} ${-v.y + dy})`}>
            <circle
              r={1.05 * u}
              fill={vehicleColor(v.id)}
              style={{ stroke: "var(--card)" }}
              strokeWidth={2}
              vectorEffect="non-scaling-stroke"
            />
            <text y={0.4 * u} fontSize={1.15 * u} fontWeight={700} textAnchor="middle" fill="#ffffff">
              {v.id}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

function Log({ run, t }: { run: ModeRun; t: number }) {
  const [open, setOpen] = useState(false);
  const [showWhy, setShowWhy] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const nLines = countUpTo(run.lines, t);
  const nNotes = showWhy ? countUpTo(run.notes ?? [], t) : 0;

  const entries = useMemo(() => {
    type Entry = { t: number; kind: 0 | 1; v: number; text: string };
    const lines: Entry[] = run.lines
      .slice(Math.max(0, nLines - LOG_LIMIT), nLines)
      .map(([time, v, text]) => ({ t: time, kind: 0, v, text }));
    const notes: Entry[] = (run.notes ?? [])
      .slice(Math.max(0, nNotes - LOG_LIMIT), nNotes)
      .map(([time, text]) => ({ t: time, kind: 1, v: -1, text }));
    // notes follow the vehicle lines of the same minute, as with --explain
    const merged: Entry[] = [];
    let i = 0;
    let j = 0;
    while (i < lines.length || j < notes.length) {
      if (j >= notes.length || (i < lines.length && lines[i].t <= notes[j].t)) merged.push(lines[i++]);
      else merged.push(notes[j++]);
    }
    return merged.slice(-LOG_LIMIT);
  }, [run, nLines, nNotes]);

  useEffect(() => {
    if (open && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [entries, open]);

  return (
    <details
      className="group rounded-xl border border-line"
      onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}
    >
      <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-3 py-2.5 text-sm font-semibold [&::-webkit-details-marker]:hidden">
        <span>Full schedule</span>
        <span className="flex items-center gap-2 text-xs font-normal text-muted">
          {nLines} event{nLines === 1 ? "" : "s"} so far
          <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden className="transition-transform group-open:rotate-180">
            <path d="M2 4l4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.5" />
          </svg>
        </span>
      </summary>
      <div className="flex flex-col gap-2 border-t border-line p-3">
        <label className="flex cursor-pointer items-center gap-2 self-start text-xs text-muted">
          <input type="checkbox" checked={showWhy} onChange={(e) => setShowWhy(e.target.checked)} />
          Show why each decision was made
        </label>
        <div ref={box} className="h-64 overflow-y-auto text-sm leading-6" aria-live="off">
          {entries.length === 0 ? (
            <p className="text-muted">Nothing yet. Press play.</p>
          ) : (
            entries.map((e, k) =>
              e.kind === 0 ? (
                <div key={k} className="flex items-baseline gap-3">
                  <span className="shrink-0 font-mono text-xs tabular-nums text-muted">{clock(e.t)}</span>
                  <span className="shrink-0 font-medium" style={{ color: vehicleColor(e.v) }}>
                    Vehicle {e.v}
                  </span>
                  <span className="min-w-0">{e.text}</span>
                </div>
              ) : (
                <div key={k} className="my-1 flex items-baseline gap-3 rounded-lg bg-background px-2 py-1 text-xs leading-5 text-muted">
                  <span className="shrink-0 font-mono tabular-nums">{clock(e.t)}</span>
                  <span className="min-w-0">{e.text}</span>
                </div>
              ),
            )
          )}
        </div>
      </div>
    </details>
  );
}
