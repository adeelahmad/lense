"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Voice for chat: hear one thing said, and say an answer aloud. This is the one seam the UI talks to; today it uses
 * the browser's own speech recognition and synthesis. A server engine (local transcription, spoken replies) replaces
 * the internals here and keeps the hook's shape, so every mic button switches over with it.
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

/** Whether this browser can listen. */
export function canListen(): boolean {
  return recognitionClass() != null;
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

export function useVoice(): Voice {
  const [supported, setSupported] = useState(false);
  const [listening, setListening] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [heard, setHeard] = useState("");
  const rec = useRef<Recognition | null>(null);
  const settle = useRef<(() => void) | null>(null);

  useEffect(() => setSupported(canListen()), []);

  const stop = useCallback(() => {
    rec.current?.abort();
    rec.current = null;
    if (typeof window !== "undefined" && window.speechSynthesis) window.speechSynthesis.cancel();
    settle.current?.();
    settle.current = null;
    setListening(false);
    setSpeaking(false);
  }, []);
  useEffect(() => stop, [stop]);

  const listen = useCallback(
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

  const speak = useCallback(
    (text: string) =>
      new Promise<void>((resolve) => {
        const synth = typeof window !== "undefined" ? window.speechSynthesis : undefined;
        const words = speakable(text);
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

  return { supported, listening, speaking, heard, listen, speak, stop };
}
