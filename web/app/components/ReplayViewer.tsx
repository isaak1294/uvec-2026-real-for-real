"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ModePanel from "./ModePanel";
import {
  type IndexEntry,
  type Replay,
  asReplay,
  atClock,
  boundsOf,
  clock,
  dayOf,
  modeKeys,
  scenarioTitle,
  timeRange,
} from "../lib/replay";

const SPEEDS = [1, 5, 15, 60, 240]; // simulated minutes per second
const UPLOAD = "upload:";

export default function ReplayViewer() {
  const [index, setIndex] = useState<IndexEntry[] | null>(null);
  const [indexError, setIndexError] = useState<string | null>(null);
  const [selected, setSelected] = useState("");
  const [loaded, setLoaded] = useState<{ key: string; replay: Replay } | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [uploads, setUploads] = useState<Record<string, Replay>>({});
  const [t, setT] = useState(0);
  const tRef = useRef(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(15);
  const startAt = useRef<string | null>(null); // ?t=HH:MM, applied to the first replay loaded

  const seek = useCallback((value: number) => {
    tRef.current = value;
    setT(value);
  }, []);

  // the list of exported replays
  useEffect(() => {
    let live = true;
    fetch("/replays/index.json")
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((d: { replays: IndexEntry[] }) => {
        if (!live) return;
        setIndex(d.replays);
        // deep link: ?replay=typical_day&t=09:35
        const q = new URLSearchParams(window.location.search);
        const want = q.get("replay") ?? "typical_day";
        const hit = d.replays.find((r) => r.name === want || r.file === want) ?? d.replays[0];
        startAt.current = q.get("t");
        if (hit) setSelected((s) => s || hit.file);
      })
      .catch((e: Error) => live && setIndexError(e.message));
    return () => {
      live = false;
    };
  }, []);

  // the selected exported replay
  useEffect(() => {
    if (!selected || selected.startsWith(UPLOAD)) return;
    let live = true;
    fetch(`/replays/${selected}`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((d: unknown) => {
        if (!live) return;
        const replay = asReplay(d);
        const [lo, hi] = timeRange(replay);
        setLoaded({ key: selected, replay });
        setLoadError(null);
        setPlaying(false);
        seek(atClock(startAt.current, lo, hi) ?? lo);
        startAt.current = null;
      })
      .catch((e: Error) => live && setLoadError(`${selected}: ${e.message}`));
    return () => {
      live = false;
    };
  }, [selected, seek]);

  const replay = selected.startsWith(UPLOAD)
    ? (uploads[selected] ?? null)
    : loaded?.key === selected
      ? loaded.replay
      : null;
  const [tMin, tMax] = useMemo(() => (replay ? timeRange(replay) : [0, 0]), [replay]);
  const bounds = useMemo(() => (replay ? boundsOf(replay) : null), [replay]);
  const now = Math.min(Math.max(t, tMin), tMax);

  // playback: advance the shared clock in real time
  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    let last: number | null = null;
    const tick = (stamp: number) => {
      if (last !== null) {
        const next = Math.min(tRef.current + ((stamp - last) / 1000) * speed, tMax);
        tRef.current = next;
        setT(next);
        if (next >= tMax) {
          setPlaying(false);
          return;
        }
      }
      last = stamp;
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, speed, tMax]);

  const togglePlay = useCallback(() => {
    if (!replay) return;
    if (!playing && tRef.current >= tMax) seek(tMin);
    setPlaying(!playing);
  }, [replay, playing, tMax, tMin, seek]);

  const stepBy = useCallback(
    (d: number) => {
      setPlaying(false);
      seek(Math.min(Math.max(Math.floor(tRef.current) + d, tMin), tMax));
    },
    [seek, tMin, tMax],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA" || tag === "BUTTON" || tag === "SUMMARY") return;
      if (e.key === " ") {
        e.preventDefault();
        togglePlay();
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        stepBy(-1);
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        stepBy(1);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [togglePlay, stepBy]);

  const pick = (key: string) => {
    setPlaying(false);
    setSelected(key);
    if (key.startsWith(UPLOAD)) {
      const r = uploads[key];
      if (r) seek(timeRange(r)[0]);
    }
  };

  const onFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    file
      .text()
      .then((text) => {
        const r = asReplay(JSON.parse(text));
        const key = `${UPLOAD}${file.name}`;
        setUploads((u) => ({ ...u, [key]: r }));
        setSelected(key);
        setLoadError(null);
        setPlaying(false);
        seek(timeRange(r)[0]);
      })
      .catch((err: Error) => setLoadError(`${file.name}: ${err.message}`));
  };

  const keys = replay ? modeKeys(replay) : [];
  const tabs = [
    ...(index ?? []).map((r) => ({ key: r.file, title: scenarioTitle(r.name), vehicles: r.vehicles, calls: r.calls })),
    ...Object.entries(uploads).map(([key, r]) => ({
      key,
      title: scenarioTitle(r.name),
      vehicles: r.vehicles,
      calls: r.calls.length,
    })),
  ];

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-4 py-8 sm:px-6 sm:py-10">
      <header className="flex flex-col gap-2">
        <p className="text-sm font-medium text-muted">UVEC 2026 · Emergency dispatch</p>
        <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">Dispatch replay</h1>
        <p className="max-w-2xl text-base leading-relaxed text-muted">
          One day of emergency calls, handled two ways on the same clock. Calls appear only when they come in,
          exactly as the dispatcher saw them. Less waiting is better.
        </p>
      </header>

      <nav aria-label="Scenario" className="-mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0">
        <ul className="flex gap-2 pb-1">
          {!tabs.length && !indexError && <li className="py-2 text-sm text-muted">Loading scenarios…</li>}
          {tabs.map((tab) => {
            const on = tab.key === selected;
            return (
              <li key={tab.key} className="shrink-0">
                <button
                  type="button"
                  aria-pressed={on}
                  onClick={() => pick(tab.key)}
                  className={`flex flex-col items-start rounded-xl border px-4 py-2.5 text-left transition-colors ${
                    on
                      ? "border-foreground bg-foreground text-background"
                      : "border-line bg-card hover:border-muted"
                  }`}
                >
                  <span className="text-sm font-semibold">{tab.title}</span>
                  <span className={`text-xs ${on ? "opacity-75" : "text-muted"}`}>
                    {tab.vehicles} vehicle{tab.vehicles === 1 ? "" : "s"} · {tab.calls} calls
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </nav>

      {indexError && (
        <Message>
          Couldn&apos;t load the scenario list ({indexError}). Export some first: in the solution folder run{" "}
          <code className="font-mono">python replay.py inputs/*.txt</code>, or open a replay file below.
        </Message>
      )}
      {loadError && <Message>Couldn&apos;t open {loadError}</Message>}
      {index && index.length === 0 && !replay && (
        <Message>
          No replays exported yet. In the solution folder run <code className="font-mono">python replay.py inputs/*.txt</code>.
        </Message>
      )}

      {replay && bounds && (
        <>
          <Summary replay={replay} keys={keys} />

          <Player
            now={now}
            tMin={tMin}
            tMax={tMax}
            calls={replay.calls.map((c) => c.t)}
            playing={playing}
            speed={speed}
            onToggle={togglePlay}
            onStep={stepBy}
            onSeek={(v) => {
              setPlaying(false);
              seek(v);
            }}
            onSpeed={setSpeed}
          />

          <Legend />

          <div className="grid gap-6 lg:grid-cols-2">
            {keys.map((k) => (
              <ModePanel key={k} modeKey={k} replay={replay} run={replay.modes[k]} t={now} bounds={bounds} />
            ))}
          </div>
        </>
      )}
      {!replay && !indexError && !loadError && <p className="text-sm text-muted">Loading replay…</p>}

      <footer className="mt-4 flex flex-col gap-2 border-t border-line pt-6 text-sm text-muted sm:flex-row sm:items-center sm:justify-between">
        <p>1 block of the grid = 1 minute of driving. Base is at the centre.</p>
        <label className="cursor-pointer self-start underline decoration-line underline-offset-4 hover:text-foreground sm:self-auto">
          Open your own replay file…
          <input type="file" accept=".json,application/json" className="sr-only" onChange={onFile} />
        </label>
      </footer>
    </div>
  );
}

function Message({ children }: { children: React.ReactNode }) {
  return (
    <p className="rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
      {children}
    </p>
  );
}

function Summary({ replay, keys }: { replay: Replay; keys: string[] }) {
  const n = replay.calls.length || 1;
  const total = (k: string) => replay.modes[k].stats.total_delay;
  const max = Math.max(1, ...keys.map(total));
  const best = Math.min(...keys.map(total));
  const show = (v: number) => (Number.isInteger(v) ? String(v) : v.toFixed(1));

  let headline = "Total waiting over the day";
  let sub = `${replay.vehicles} vehicles, ${replay.calls.length} calls.`;
  if (keys.length === 2) {
    const [a, b] = keys.map((k) => replay.modes[k]);
    const diff = Math.abs(a.stats.total_delay - b.stats.total_delay);
    if (diff === 0) {
      headline = `Both ways tie at ${a.stats.total_delay} min of total waiting`;
    } else {
      const [win, lose] = a.stats.total_delay < b.stats.total_delay ? [a, b] : [b, a];
      const pct = Math.round((100 * diff) / Math.max(1, lose.stats.total_delay));
      headline = `${win.label} saves ${diff} min of waiting`;
      sub = `${pct}% less than ${lose.label.toLowerCase()}, across ${replay.calls.length} calls with ${replay.vehicles} vehicle${replay.vehicles === 1 ? "" : "s"}.`;
    }
  }

  const facts: [string, (k: string) => number, string][] = [
    ["Average wait per call", (k) => total(k) / n, "min"],
    ["Longest wait", (k) => replay.modes[k].stats.max_delay, "min"],
    ["Distance driven", (k) => replay.modes[k].stats.travel, "blocks"],
  ];

  return (
    <section aria-labelledby="summary-title" className="rounded-2xl border border-line bg-card p-5 sm:p-6">
      <h2 id="summary-title" className="text-xl font-semibold tracking-tight sm:text-2xl">
        {headline}
      </h2>
      <p className="mt-1 text-sm text-muted">{sub}</p>

      <div className="mt-5 flex flex-col gap-3">
        {keys.map((k) => (
          <div key={k} className="grid grid-cols-[7.5rem_1fr_auto] items-center gap-3 sm:grid-cols-[9rem_1fr_auto]">
            <span className="text-sm font-medium">{replay.modes[k].label}</span>
            <span className="h-3 overflow-hidden rounded-full bg-background">
              <span
                className="block h-full rounded-full"
                style={{
                  width: `${(100 * total(k)) / max}%`,
                  background: total(k) === best ? "var(--foreground)" : "var(--axis)",
                }}
              />
            </span>
            <span className="font-mono text-sm tabular-nums">{total(k)} min</span>
          </div>
        ))}
      </div>

      <dl className="mt-6 grid gap-4 border-t border-line pt-5 sm:grid-cols-3">
        {facts.map(([label, get, unit]) => (
          <div key={label} className="flex flex-col gap-1.5">
            <dt className="text-xs font-medium uppercase tracking-wide text-muted">{label}</dt>
            {keys.map((k) => (
              <dd key={k} className="flex items-baseline justify-between gap-2 text-sm">
                <span className="text-muted">{replay.modes[k].label}</span>
                <span className="font-mono tabular-nums">
                  {show(get(k))} {unit}
                </span>
              </dd>
            ))}
          </div>
        ))}
      </dl>
    </section>
  );
}

interface PlayerProps {
  now: number;
  tMin: number;
  tMax: number;
  calls: number[];
  playing: boolean;
  speed: number;
  onToggle: () => void;
  onStep: (d: number) => void;
  onSeek: (t: number) => void;
  onSpeed: (s: number) => void;
}

function Player({ now, tMin, tMax, calls, playing, speed, onToggle, onStep, onSeek, onSpeed }: PlayerProps) {
  const span = Math.max(1, tMax - tMin);
  const frac = (x: number) => (x - tMin) / span;
  const day = dayOf(now);
  const iconBtn =
    "flex size-11 items-center justify-center rounded-full border border-line bg-card text-foreground hover:border-muted";

  return (
    <section
      aria-label="Playback"
      className="sticky top-0 z-10 -mx-4 flex flex-col gap-3 border-y border-line bg-card/90 px-4 py-3 backdrop-blur sm:mx-0 sm:rounded-2xl sm:border sm:px-5"
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-3">
        <div className="flex items-center gap-2">
          <button type="button" onClick={() => onStep(-1)} aria-label="Back one minute" className={iconBtn}>
            <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden>
              <path d="M3 3h2v10H3zM13 3v10L6 8z" />
            </svg>
          </button>
          <button
            type="button"
            onClick={onToggle}
            aria-label={playing ? "Pause" : "Play"}
            className="flex size-12 items-center justify-center rounded-full bg-foreground text-background hover:opacity-90"
          >
            {playing ? (
              <svg width="18" height="18" viewBox="0 0 16 16" fill="currentColor" aria-hidden>
                <path d="M4 2h3v12H4zM9 2h3v12H9z" />
              </svg>
            ) : (
              <svg width="18" height="18" viewBox="0 0 16 16" fill="currentColor" aria-hidden>
                <path d="M4 2l10 6-10 6z" />
              </svg>
            )}
          </button>
          <button type="button" onClick={() => onStep(1)} aria-label="Forward one minute" className={iconBtn}>
            <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden>
              <path d="M11 3h2v10h-2zM3 3v10l7-5z" />
            </svg>
          </button>
        </div>

        <p className="flex items-baseline gap-2" aria-live="off">
          <span className="font-mono text-3xl font-semibold tabular-nums">{clock(now)}</span>
          {day > 0 && <span className="text-xs font-medium text-muted">next day</span>}
        </p>

        <div role="group" aria-label="Speed, simulated minutes per second" className="ml-auto flex items-center gap-2">
          <span className="text-xs text-muted">Speed</span>
          <div className="flex rounded-full border border-line p-0.5">
            {SPEEDS.map((s) => (
              <button
                key={s}
                type="button"
                aria-pressed={s === speed}
                title={`${s} simulated minute${s === 1 ? "" : "s"} per second`}
                onClick={() => onSpeed(s)}
                className={`min-w-10 rounded-full px-2.5 py-1.5 font-mono text-xs tabular-nums ${
                  s === speed ? "bg-foreground text-background" : "text-muted hover:text-foreground"
                }`}
              >
                {s}×
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="flex flex-col">
        <div className="relative">
          {/* one tick per call, above the track */}
          <div aria-hidden className="pointer-events-none absolute inset-x-2 top-0 h-2">
            {calls.map((ct, i) => (
              <span
                key={i}
                className="absolute top-0 h-2 w-px"
                style={{ left: `${100 * frac(ct)}%`, background: ct <= now ? "var(--foreground)" : "var(--axis)" }}
              />
            ))}
          </div>
          <input
            type="range"
            className="timeline w-full"
            style={{ "--fill": `${100 * frac(now)}%` } as React.CSSProperties}
            min={tMin}
            max={tMax}
            step={1}
            value={Math.floor(now)}
            aria-label="Time"
            aria-valuetext={`${clock(now)}${day ? ", next day" : ""}`}
            onChange={(e) => onSeek(Number(e.target.value))}
          />
        </div>
        <div className="flex justify-between font-mono text-xs text-muted tabular-nums">
          <span>{clock(tMin)}</span>
          <span className="hidden font-sans sm:inline">Ticks mark when calls came in · space plays, ← → step a minute</span>
          <span>{clock(tMax)}</span>
        </div>
      </div>
    </section>
  );
}

function Legend() {
  const item = (swatch: React.ReactNode, text: string) => (
    <li className="flex items-center gap-2">
      <svg width="16" height="16" viewBox="-8 -8 16 16" aria-hidden>
        {swatch}
      </svg>
      {text}
    </li>
  );
  const square = (fill: string, stroke = "none") => (
    <rect x={-5} y={-5} width={10} height={10} rx={2} style={{ fill, stroke }} strokeWidth={2} />
  );
  return (
    <ul aria-label="Map key" className="flex flex-wrap gap-x-6 gap-y-2 text-sm text-muted">
      {item(
        <rect x={-6} y={-6} width={12} height={12} rx={2} style={{ fill: "var(--card)", stroke: "var(--foreground)" }} strokeWidth={2} />,
        "Base",
      )}
      {item(<circle r={7} fill="#2563eb" />, "Vehicle")}
      {item(square("var(--waiting)"), "Call waiting for a vehicle")}
      {item(square("var(--card)", "#2563eb"), "Vehicle on its way")}
      {item(square("#2563eb"), "Being helped")}
      {item(square("var(--done)"), "Done")}
    </ul>
  );
}
