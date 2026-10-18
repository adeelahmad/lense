import type { Enhanced } from "@/app/openapi-client/types.gen";

/** Why the enhance step thought an image looked low quality (docs/enhance.md). */
export const ENHANCE_REASONS: Record<string, string> = {
  small: "small",
  blurry: "blurry",
  noisy: "noisy",
  dull: "low contrast",
  small_faces: "faces too small",
};

/** The engine that made the copy, as people know it. */
export function engineName(engine: string | null | undefined): string | null {
  if (!engine) return null;
  return engine === "classic" ? "the classic engine" : engine.startsWith("realesr") ? "Real-ESRGAN" : engine;
}

/** One line for the Details tab: what the enhance step found and did ("not run" before it ran). */
export function enhanceText(e: Enhanced | null | undefined): string {
  if (!e) return "not run";
  const m = e.measured;
  const was = `${m.width}×${m.height}`;
  if (!e.enhanced) return `good enough as it is (${was})`;
  const why = (e.low ?? []).map((r) => ENHANCE_REASONS[r] ?? r).join(", ");
  const made = e.width && e.height ? `${was} to ${e.width}×${e.height}` : was;
  return [
    why ? `${why[0].toUpperCase()}${why.slice(1)}` : "On request",
    [made, engineName(e.engine) && `by ${engineName(e.engine)}`].filter(Boolean).join(" "),
  ].join(" · ");
}
