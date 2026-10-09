"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Voice as VoiceApi } from "@/app/openapi-client";
import type { Client } from "@/app/openapi-client/client";
import { data, useApiClient } from "@/lib/api/browser";

/**
 * Voice for chat: hear one thing said, and say an answer aloud. This is the one seam the UI talks to. When the server
 * has a speech-to-text engine (GET /voice), what's said is recorded here and turned into text there, so it stays on
 * the server, with what's been heard so far shown while talking; else the browser's own speech recognition listens.
 * Answers are read by the server's speech model when it has one, else by the browser.
 */

type Recognition = {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  onresult:
    | ((e: {
        resultIndex: number;
        results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal: boolean }>;
      }) => void)
    | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
  abort: () => void;
};

function recognitionClass(): (new () => Recognition) | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as Record<string, unknown>;
  return ((w.SpeechRecognition ?? w.webkitSpeechRecognition) as (new () => Recognition) | undefined) ?? null;
}

/** Whether this browser can record the mic for the server to transcribe. */
export function canRecord(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.MediaRecorder !== "undefined" &&
    Boolean(navigator.mediaDevices?.getUserMedia)
  );
}

/** Whether this browser can listen: by its own recognition, or by recording for the server. */
export function canListen(): boolean {
  return recognitionClass() != null || canRecord();
}

/** Why listening stopped, in words for a toast (null: nothing to tell). */
export function voiceError(code: string): string | null {
  if (code === "no-speech" || code === "aborted") return null;
  if (code === "not-allowed" || code === "service-not-allowed")
    return "The microphone is blocked. Allow it for this site in the browser, then try again.";
  if (code === "audio-capture") return "No microphone was found.";
  if (code === "network") return "Speech recognition needs a connection right now.";
  return "Listening stopped.";
}

/** Text to read aloud: no citation marks, no markdown punctuation. */
export function speakable(text: string): string {
  return text
    .replace(/\s*\[\d+(?:,\s*\d+)*\]/g, "")
    .replace(/```[\s\S]*?```/g, "")
    .replace(/[*_`#>]+/g, "")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/\s+/g, " ")
    .trim();
}

export class VoiceError extends Error {
  constructor(public code: string) {
    super(voiceError(code) ?? code);
  }
}

export type Voice = {
  /** This browser can listen. */
  supported: boolean;
  listening: boolean;
  speaking: boolean;
  /** What's been heard so far, while listening. */
  heard: string;
  /** Listens until a pause; resolves with what was said ("" for nothing). Rejects with a VoiceError. */
  listen: () => Promise<string>;
  /** Says the text aloud; resolves when done or cut off. */
  speak: (text: string) => Promise<void>;
  /** Stops listening and speaking at once. */
  stop: () => void;
};

type ServerVoice = { transcribe: boolean; speak: boolean };
let serverVoice: Promise<ServerVoice> | null = null;

/** What the server does itself (asked once per page load; asking also starts loading its engine). */
function askServer(client: Client): Promise<ServerVoice> {
  serverVoice ??= data(VoiceApi.voiceInfo({ client }))
    .then((v) => ({ transcribe: v.transcribe, speak: v.speak }))
    .catch(() => {
      serverVoice = null;
      return { transcribe: false, speak: false };
    });
  return serverVoice;
}

/** How long a pause ends what's being said, how long to wait for anything at all, and the longest turn (ms). */
export const PAUSE_MS = 1200;
export const WAIT_MS = 7000;
export const MAX_MS = 60_000;
const PARTIAL_MS = 1500;

function mimeType(): string | undefined {
  const R = typeof window !== "undefined" ? window.MediaRecorder : undefined;
  return ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"].find((t) =>
    R?.isTypeSupported?.(t),
  );
}

function micError(e: unknown): VoiceError {
  const name = e instanceof Error ? e.name : "";
  if (name === "NotAllowedError" || name === "SecurityError") return new VoiceError("not-allowed");
  if (name === "NotFoundError" || name === "OverconstrainedError") return new VoiceError("audio-capture");
  return new VoiceError("busy");
}

/** Loudness (RMS) of what the mic hears now. */
function level(an: AnalyserNode, buf: Float32Array<ArrayBuffer>): number {
  an.getFloatTimeDomainData(buf);
  let sum = 0;
  for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
  return Math.sqrt(sum / buf.length);
}

export function useVoice(): Voice {
  const client = useApiClient();

  const [supported, setSupported] = useState(false);
  const [listening, setListening] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [heard, setHeard] = useState("");
  const rec = useRef<Recognition | null>(null);
  const settle = useRef<(() => void) | null>(null);
  const cut = useRef<(() => void) | null>(null);
  const audio = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    let live = true;
    setSupported(recognitionClass() != null);
    void askServer(client).then((v) => {
      if (!live) return;

      setSupported(recognitionClass() != null || (v.transcribe && canRecord()));
    });
    return () => {
      live = false;
    };
  }, [client]);

  const stop = useCallback(() => {
    rec.current?.abort();
    rec.current = null;
    cut.current?.();
    cut.current = null;
    audio.current?.pause();
    audio.current = null;
    if (typeof window !== "undefined" && window.speechSynthesis) window.speechSynthesis.cancel();
    settle.current?.();
    settle.current = null;
    setListening(false);
    setSpeaking(false);
  }, []);
  useEffect(() => stop, [stop]);

  /** The browser's own recognition. */
  const listenHere = useCallback(
    () =>
      new Promise<string>((resolve, reject) => {
        const R = recognitionClass();
        if (!R) return reject(new VoiceError("unsupported"));
        rec.current?.abort();
        const r = new R();
        rec.current = r;
        r.lang = typeof navigator !== "undefined" ? navigator.language || "en-US" : "en-US";
        r.interimResults = true;
        r.continuous = false;
        let text = "";
        let failed: string | null = null;
        r.onresult = (e) => {
          text = Array.from(e.results)
            .map((x) => x[0]?.transcript ?? "")
            .join("");
          setHeard(text);
        };
        r.onerror = (e) => {
          failed = e.error;
        };
        r.onend = () => {
          if (rec.current === r) rec.current = null;
          setListening(false);
          setHeard("");
          if (failed && voiceError(failed)) reject(new VoiceError(failed));
          else resolve(text.trim());
        };
        setHeard("");
        setListening(true);
        try {
          r.start();
        } catch {
          setListening(false);
          reject(new VoiceError("busy"));
        }
      }),
    [],
  );

  /** Recorded here, turned into text by the server: ends at a pause after speech, or when nothing is said a while. */
  const listenServer = useCallback(async (): Promise<string> => {
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    } catch (e) {
      throw micError(e);
    }
    const type = mimeType();
    const recorder = new MediaRecorder(stream, type ? { mimeType: type } : undefined);
    const chunks: Blob[] = [];
    const ctx = new AudioContext();
    const an = ctx.createAnalyser();
    an.fftSize = 2048;
    ctx.createMediaStreamSource(stream).connect(an);
    const buf = new Float32Array(an.fftSize);
    const clip = () => new Blob(chunks, { type: recorder.mimeType || type || "audio/webm" });
    const toText = async (b: Blob) => (await data(VoiceApi.transcribeClip({ client, body: b }))).text;

    setHeard("");
    setListening(true);
    return new Promise<string>((resolve, reject) => {
      const began = Date.now();
      let floor = 0;
      let samples = 0;
      let spoke = 0; // when speech started (0: not yet)
      let lastLoud = 0;
      let partial = false;
      let lastPartial = 0;
      let cancelled = false;
      let done = false;
      const end = async () => {
        stream.getTracks().forEach((t) => t.stop());
        void ctx.close().catch(() => undefined);
        if (cut.current === cancel) cut.current = null;
        try {
          resolve(cancelled || !spoke ? "" : (await toText(clip())).trim());
        } catch (e) {
          reject(e instanceof Error ? e : new VoiceError("network"));
        } finally {
          setListening(false);
          setHeard("");
        }
      };
      const finish = () => {
        if (done) return;
        done = true;
        clearInterval(tick);
        if (recorder.state !== "inactive") recorder.stop();
        else void end();
      };
      const cancel = () => {
        cancelled = true;
        finish();
      };
      cut.current = cancel;
      recorder.ondataavailable = (e) => {
        if (e.data.size) chunks.push(e.data);
      };
      recorder.onstop = () => void end();
      recorder.start(250);
      const tick = setInterval(() => {
        const now = Date.now();
        const v = level(an, buf);
        if (samples < 3) {
          floor = (floor * samples + v) / (samples + 1); // the room's own noise, from the first moments
          samples++;
          return;
        }
        if (v > Math.max(0.015, floor * 2.5)) {
          lastLoud = now;
          if (!spoke) spoke = now;
        }
        if (spoke && now - lastLoud > PAUSE_MS) return finish();
        if (!spoke && now - began > WAIT_MS) return finish();
        if (now - began > MAX_MS) return finish();
        if (spoke && !partial && now - lastPartial > PARTIAL_MS && chunks.length) {
          partial = true;
          lastPartial = now;
          toText(clip())
            .then((t) => {
              if (!done) setHeard(t);
            })
            .catch(() => undefined)
            .finally(() => {
              partial = false;
            });
        }
      }, 100);
    });
  }, [client]);

  const listen = useCallback(async () => {
    const s = await askServer(client);
    return s.transcribe && canRecord() ? listenServer() : listenHere();
  }, [client, listenServer, listenHere]);

  /** The browser reads it. */
  const speakHere = useCallback(
    (words: string) =>
      new Promise<void>((resolve) => {
        const synth = typeof window !== "undefined" ? window.speechSynthesis : undefined;
        if (!synth || !words || typeof SpeechSynthesisUtterance === "undefined") return resolve();
        const u = new SpeechSynthesisUtterance(words);
        u.lang = navigator.language || "en-US";
        const done = () => {
          if (settle.current === done) settle.current = null;
          setSpeaking(false);
          resolve();
        };
        settle.current = done;
        u.onend = done;
        u.onerror = done;
        setSpeaking(true);
        synth.cancel();
        synth.speak(u);
      }),
    [],
  );

  /** The server's speech model reads it; false when it has none or that failed. */
  const speakServer = useCallback(
    async (words: string): Promise<boolean> => {
      let clip: Blob | null = null;
      try {
        const r = await VoiceApi.speakText({ client, body: { text: words }, parseAs: "blob" });
        if (r.response?.status === 200 && r.data instanceof Blob && r.data.size) clip = r.data;
      } catch {
        return false;
      }
      if (!clip) return false;
      const url = URL.createObjectURL(clip);
      await new Promise<void>((resolve) => {
        const a = new Audio(url);
        audio.current = a;
        const done = () => {
          if (settle.current === done) settle.current = null;
          URL.revokeObjectURL(url);
          setSpeaking(false);
          resolve();
        };
        settle.current = done;
        a.onended = done;
        a.onerror = done;
        setSpeaking(true);
        a.play().catch(done);
      });
      return true;
    },
    [client],
  );

  const speak = useCallback(
    async (text: string) => {
      const words = speakable(text);
      if (!words) return;
      if ((await askServer(client)).speak && (await speakServer(words))) return;
      return speakHere(words);
    },
    [client, speakServer, speakHere],
  );

  return { supported, listening, speaking, heard, listen, speak, stop };
}
