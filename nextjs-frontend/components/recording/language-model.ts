/**
 * An asset's language in its Details tab (docs/api.md, An asset's language): how it reads, and what a person's edit
 * sends. Pure functions.
 */
import { languageName } from "@/components/library/model";
import { languageProblem } from "@/components/recording/files-model";
import { tidyLanguage } from "@/components/recording/translate-model";

/** What engines say when there's no language to give, not a language. */
const NOT_A_LANGUAGE = ["none", "nospeech"];

/** The asset's language, or null when it isn't known. */
export function knownLanguage(code: string | null | undefined): string | null {
  return code && !NOT_A_LANGUAGE.includes(code.toLowerCase()) ? code : null;
}

/** "British English (en-GB)", "English (en)"; an odd code on its own; null when it isn't known. */
export function languageLine(code: string | null | undefined): string | null {
  const known = knownLanguage(code);
  if (!known) return null;
  const tag = tidyLanguage(known);
  const name = languageName(tag);
  return name === tag ? tag : `${name} (${tag})`;
}

/** What saving `draft` sends ({language: null} forgets it), "same" when nothing changes, or why it won't do. */
export function languageEdit(
  draft: string,
  current: string | null | undefined,
): { language: string | null } | "same" | { problem: string } {
  const v = draft.trim();
  if (!v) return knownLanguage(current) ? { language: null } : "same";
  const problem = languageProblem(v.replace(/_/g, "-"));
  if (problem) return { problem: "Use a language tag such as en, en-GB or pt-BR." };
  const tag = tidyLanguage(v);
  return current && tag === tidyLanguage(current) ? "same" : { language: tag };
}
