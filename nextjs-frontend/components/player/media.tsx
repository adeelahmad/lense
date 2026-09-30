"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

/**
 * One media clock for the recording page: the <audio>/<video> element when there is one, or a cursor when there is
 * only a transcript. Coarse state (time at ~4 Hz, playing, rate, volume) drives React; the playhead and other
 * per-frame visuals subscribe to a requestAnimationFrame feed instead (no React state per frame).
 */

export type SeekOptions = { manual?: boolean };
type Tick = (ms: number) => void;

export type PlayerApi = {
  /** Attach the <audio>/<video> element (a callback ref). */
  attach: (el: HTMLMediaElement | null) => void;
  seek: (ms: number, opts?: SeekOptions) => void;
  seekBy: (ms: number, opts?: SeekOptions) => void;
  play: () => void;
  pause: () => void;
  toggle: () => void;
  setRate: (r: number) => void;
  setVolume: (v: number) => void;
  setMuted: (m: boolean) => void;
  setSkipSilence: (on: boolean) => void;
  /** The precise current time, in ms. */
  now: () => number;
  /** Per-frame time feed while playing (and once after every seek). Returns an unsubscribe. */
  subscribe: (fn: Tick) => () => void;
  element: () => HTMLMediaElement | null;
};

export type PlayerState = {
  /** Whether a playable file is attached (false for transcript-only recordings). */
  hasMedia: boolean;
  status: "none" | "loading" | "ready" | "error";
  playing: boolean;
  /** Current time in ms, updated a few times a second and on every seek. */
  time: number;
  duration: number;
  rate: number;
  volume: number;
  muted: boolean;
  skipSilence: boolean;
};

const ApiCtx = createContext<PlayerApi | null>(null);
const StateCtx = createContext<PlayerState | null>(null);

export function usePlayerApi(): PlayerApi {
  const c = useContext(ApiCtx);
  if (!c) throw new Error("usePlayerApi needs a PlayerProvider");
  return c;
}

export function usePlayerState(): PlayerState {
  const c = useContext(StateCtx);
  if (!c) throw new Error("usePlayerState needs a PlayerProvider");
  return c;
}

const PREF = "lens.player";

function loadPrefs(): { rate: number; volume: number; skipSilence: boolean } {
  try {
    const p = JSON.parse(localStorage.getItem(PREF) || "{}") as Record<string, unknown>;
    return {
      rate: typeof p.rate === "number" && p.rate >= 0.5 && p.rate <= 3 ? p.rate : 1,
      volume: typeof p.volume === "number" && p.volume >= 0 && p.volume <= 1 ? p.volume : 1,
      skipSilence: p.skipSilence === true,
    };
  } catch {
    return { rate: 1, volume: 1, skipSilence: false };
  }
}

function savePrefs(p: { rate: number; volume: number; skipSilence: boolean }) {
  try {
    localStorage.setItem(PREF, JSON.stringify(p));
  } catch {
    /* storage unavailable: preferences last for this visit */
  }
}

/** Where speech resumes after a pause: gaps between segments longer than `minGap`, as [end, nextStart] in ms. */
export function silenceGaps(segments: { t0: number; t1: number }[], minGap = 1500): [number, number][] {
  const out: [number, number][] = [];
  let end = 0;
  for (const s of segments) {
    if (s.t0 - end >= minGap) out.push([end, s.t0]);
    end = Math.max(end, s.t1);
  }
  return out;
}

/** If `t` is inside a gap (with a little lead-in kept), the time to jump to; else null. */
export function skipTarget(gaps: [number, number][], t: number, lead = 250): number | null {
  for (const [a, b] of gaps) {
    if (t >= a + lead && t < b - lead) return b - lead;
    if (a > t) break;
  }
  return null;
}

export function PlayerProvider({
  hasMedia,
  durationMs,
  speech,
  onSeek,
  children,
}: {
  hasMedia: boolean;
  durationMs: number;
  /** Segment timings, for "skip silence". */
  speech?: { t0: number; t1: number }[];
  /** Called after every seek; `manual` is true for seeks a person asked for (to announce the line). */
  onSeek?: (ms: number, manual: boolean) => void;
  children: ReactNode;
}) {
  const el = useRef<HTMLMediaElement | null>(null);
  const cursor = useRef(0);
  const subs = useRef(new Set<Tick>());
  const raf = useRef<number | null>(null);
  const onSeekRef = useRef(onSeek);
  const gaps = useRef<[number, number][]>([]);
  const skipRef = useRef(false);
  const durationRef = useRef(durationMs);
  const attached = useRef(new WeakSet<HTMLMediaElement>());
  const [state, setState] = useState<PlayerState>({
    hasMedia,
    status: hasMedia ? "loading" : "none",
    playing: false,
    time: 0,
    duration: durationMs,
    rate: 1,
    volume: 1,
    muted: false,
    skipSilence: false,
  });

  useEffect(() => {
    onSeekRef.current = onSeek;
  }, [onSeek]);
  useEffect(() => {
    gaps.current = silenceGaps(speech ?? []);
  }, [speech]);
  useEffect(() => {
    const p = loadPrefs();
    skipRef.current = p.skipSilence;
    setState((s) => ({ ...s, rate: p.rate, volume: p.volume, skipSilence: p.skipSilence }));
  }, []);
  useEffect(() => {
    setState((s) => ({ ...s, hasMedia, status: hasMedia ? (s.status === "none" ? "loading" : s.status) : "none", duration: Math.max(durationMs, s.hasMedia ? s.duration : 0) }));
  }, [hasMedia, durationMs]);
  useEffect(() => {
    durationRef.current = state.duration;
  }, [state.duration]);

  const now = useCallback(() => (el.current ? el.current.currentTime * 1000 : cursor.current), []);
  const emit = useCallback((ms: number) => subs.current.forEach((fn) => fn(ms)), []);

  const loop = useCallback(() => {
    const m = el.current;
    if (!m) return;
    let t = m.currentTime * 1000;
    if (skipRef.current && !m.paused) {
      const to = skipTarget(gaps.current, t);
      if (to != null) {
        m.currentTime = to / 1000;
        t = to;
      }
    }
    emit(t);
    raf.current = m.paused ? null : requestAnimationFrame(loop);
  }, [emit]);

  const seek = useCallback(
    (ms: number, opts?: SeekOptions) => {
      const m = el.current;
      const dur = m && Number.isFinite(m.duration) && m.duration > 0 ? m.duration * 1000 : durationRef.current;
      const t = Math.max(0, Math.min(ms, dur || ms));
      if (m) m.currentTime = t / 1000;
      cursor.current = t;
      setState((s) => ({ ...s, time: t }));
      emit(t);
      onSeekRef.current?.(t, Boolean(opts?.manual));
    },
    [emit],
  );

  const api = useMemo<PlayerApi>(() => {
    const play = () => {
      const m = el.current;
      if (!m) return;
      void m.play().catch(() => setState((s) => ({ ...s, playing: false })));
    };
    const pause = () => el.current?.pause();
    return {
      attach: (m) => {
        el.current = m;
        if (!m || attached.current.has(m)) return;
        attached.current.add(m);
        const p = loadPrefs();
        m.playbackRate = p.rate;
        m.volume = p.volume;
        const sync = () =>
          setState((s) => ({
            ...s,
            time: m.currentTime * 1000,
            playing: !m.paused,
            duration: Number.isFinite(m.duration) && m.duration > 0 ? m.duration * 1000 : s.duration,
          }));
        let restored = false;
        m.addEventListener("loadedmetadata", () => {
          setState((s) => ({ ...s, status: "ready", duration: Number.isFinite(m.duration) && m.duration > 0 ? m.duration * 1000 : s.duration }));
          // A seek made before the file was ready (e.g. ?t=) applies once it is; only once, so a reload can't loop.
          if (!restored && cursor.current > 0 && Math.abs(m.currentTime * 1000 - cursor.current) > 500) m.currentTime = cursor.current / 1000;
          restored = true;
        });
        m.addEventListener("error", () => setState((s) => ({ ...s, status: "error", playing: false })));
        m.addEventListener("timeupdate", sync);
        m.addEventListener("play", () => {
          sync();
          if (raf.current == null) raf.current = requestAnimationFrame(loop);
        });
        m.addEventListener("pause", sync);
        m.addEventListener("ended", sync);
        m.addEventListener("seeked", () => emit(m.currentTime * 1000));
        m.addEventListener("ratechange", () => setState((s) => ({ ...s, rate: m.playbackRate })));
        m.addEventListener("volumechange", () => setState((s) => ({ ...s, volume: m.volume, muted: m.muted })));
      },
      seek,
      seekBy: (ms, opts) => seek(now() + ms, opts),
      play,
      pause,
      toggle: () => {
        const m = el.current;
        if (!m) return;
        if (m.paused) play();
        else m.pause();
      },
      setRate: (r) => {
        if (el.current) el.current.playbackRate = r;
        setState((s) => {
          savePrefs({ rate: r, volume: s.volume, skipSilence: s.skipSilence });
          return { ...s, rate: r };
        });
      },
      setVolume: (v) => {
        if (el.current) {
          el.current.volume = v;
          el.current.muted = v === 0;
        }
        setState((s) => {
          savePrefs({ rate: s.rate, volume: v, skipSilence: s.skipSilence });
          return { ...s, volume: v, muted: v === 0 };
        });
      },
      setMuted: (mu) => {
        if (el.current) el.current.muted = mu;
        setState((s) => ({ ...s, muted: mu }));
      },
      setSkipSilence: (on) => {
        skipRef.current = on;
        setState((s) => {
          savePrefs({ rate: s.rate, volume: s.volume, skipSilence: on });
          return { ...s, skipSilence: on };
        });
      },
      now,
      subscribe: (fn) => {
        subs.current.add(fn);
        fn(now());
        return () => subs.current.delete(fn);
      },
      element: () => el.current,
    };
  }, [emit, loop, now, seek]);

  useEffect(
    () => () => {
      if (raf.current != null) cancelAnimationFrame(raf.current);
    },
    [],
  );

  return (
    <ApiCtx.Provider value={api}>
      <StateCtx.Provider value={state}>{children}</StateCtx.Provider>
    </ApiCtx.Provider>
  );
}

/** Subscribe to the per-frame clock; the callback gets ms. Use for playheads (write styles, don't set state). */
export function usePlayerTick(fn: Tick) {
  const api = usePlayerApi();
  const ref = useRef(fn);
  useEffect(() => {
    ref.current = fn;
  }, [fn]);
  useEffect(() => api.subscribe((ms) => ref.current(ms)), [api]);
}

/** Whether the person asked for less motion (auto-scroll then jumps instead of easing). */
export function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}
