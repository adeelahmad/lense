import type { Enhanced, EnhanceMeasured, EnhanceSoundMeasured } from "@/app/openapi-client/types.gen";

/** Why the enhance step thought an image looked, or a recording sounded, low quality (docs/enhance.md). */
export const ENHANCE_REASONS: Record<string, string> = {
  small: "small",
  blurry: "blurry",
  noisy: "noisy",
  dull: "low contrast",
  small_faces: "faces too small",
  quiet: "quiet",
  clipped: "clipped",
};

/** The engine that made the copy, as people know it. */
export function engineName(engine: string | null | undefined): string | null {
  if (!engine) return null;
  if (engine === "classic") return "the classic engine";
  if (engine.startsWith("realesr")) return "Real-ESRGAN";
  if (engine === "ffmpeg") return "ffmpeg's filters";
  if (engine === "webrtc") return "WebRTC";
  return engine;
}

const sentence = (s: string) => (s ? `${s[0].toUpperCase()}${s.slice(1)}` : s);

/** One line for the Details tab: what the enhance step found and did ("not run" before it ran). */
export function enhanceText(e: Enhanced | null | undefined): string {
  if (!e) return "not run";
  const why = (e.low ?? []).map((r) => ENHANCE_REASONS[r] ?? r).join(", ");
  const by = engineName(e.engine);
  if (e.kind === "audio") {
    const m = e.measured as EnhanceSoundMeasured;
    const heard = `signal to noise ${m.snr_db} dB, level ${m.level_db} dBFS`;
    if (!e.enhanced) return `good enough as it is (${heard})`;
    return [sentence(why) || "On request", heard, by && `by ${by}`].filter(Boolean).join(" · ");
  }
  const m = e.measured as EnhanceMeasured;
  const was = `${m.width}×${m.height}`;
  if (!e.enhanced) return `good enough as it is (${was})`;
  const made = e.width && e.height ? `${was} to ${e.width}×${e.height}` : was;
  return [sentence(why) || "On request", [made, by && `by ${by}`].filter(Boolean).join(" ")].join(" · ");
}
