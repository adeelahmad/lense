/**
 * Translate… in the Files tab (docs/api.md, Translating a transcript): the language a translation is made in, guessed
 * from the languages the browser reads, why one won't do, and the estimate's words. Pure functions.
 */
import type { TranslationPlan } from "@/app/openapi-client/types.gen";
import { approxCount } from "@/components/batches/format";
import { languageName } from "@/components/library/model";
import { languageProblem } from "@/components/recording/files-model";
import { plural } from "@/lib/format";

/** Languages offered while typing, besides the ones the browser reads. */
export const COMMON_LANGUAGES = [
  "ar",
  "de",
  "en",
  "es",
  "fr",
  "hi",
  "it",
  "ja",
  "ko",
  "nl",
  "pl",
  "pt",
  "pt-BR",
  "ru",
  "sv",
  "tr",
  "uk",
  "ur",
  "zh",
  "zh-TW",
];

const primary = (code: string) => code.trim().split(/[-_]/)[0].toLowerCase();

/** A language tag as it is usually written: "pt_br" → "pt-BR", "EN" → "en". */
export function tidyLanguage(code: string): string {
  const [lang, ...rest] = code.trim().split(/[-_]/);
  if (!lang) return "";
  return [lang.toLowerCase(), ...rest.map((p) => (p.length === 2 ? p.toUpperCase() : p))].join("-");
}

/** The language to translate into first: the first one the browser reads that the transcript isn't in, or "" when
 * it reads only the transcript's own (then the person picks). */
export function defaultTarget(browser: readonly string[], source: string | null | undefined): string {
  const from = source && source !== "none" ? primary(source) : null;
  const pick = browser.map(tidyLanguage).find((code) => code && primary(code) !== from);
  return pick ?? "";
}

/** The languages to offer while typing: the browser's first, then the common ones, each once, never the transcript's. */
export function languageChoices(
  browser: readonly string[],
  source: string | null | undefined,
): { code: string; name: string }[] {
  const from = source && source !== "none" ? tidyLanguage(source) : null;
  const codes = [...new Set([...browser.map(tidyLanguage), ...COMMON_LANGUAGES])].filter((c) => c && c !== from);
  return codes.map((code) => ({ code, name: languageName(code) }));
}

/** Why this language won't do, or null. */
export function targetProblem(code: string, source: string | null | undefined): string | null {
  const v = code.trim();
  if (!v) return "Choose the language to translate into.";
  const bad = languageProblem(v);
  if (bad) return bad;
  if (source && source !== "none" && tidyLanguage(v) === tidyLanguage(source))
    return `The transcript is already in ${languageName(source)}.`;
  return null;
}

function usd(v: number): string {
  if (v < 0.01) return "less than $0.01";
  return `about $${v < 1 ? v.toFixed(3).replace(/0+$/, "") : v.toFixed(2)}`;
}

/** "12 lines in 1 call, ~1k tokens, about $0.004 (an estimate)": what translating would take, always an estimate. */
export function planLine(plan: Pick<TranslationPlan, "lines" | "llm">): string {
  const tokens = (plan.llm.input_tokens ?? 0) + (plan.llm.output_tokens ?? 0);
  const cost = plan.llm.cost != null ? usd(plan.llm.cost) : "no price for the model in Settings";
  return `${plural(plan.lines, "line")} in ${plural(plan.llm.calls, "call")}, ${approxCount(tokens)} tokens, ${cost} (an estimate)`;
}
