/** Content types as the app shows them. */

export type Rules = { extensions?: string[]; pattern?: string; min_minutes?: number; max_minutes?: number };

/** "ext .srt .vtt · name ~ podcast|episode · ≥ 10 min" */
export function rulesText(r?: Rules | null): string {
  if (!r) return "";
  const parts: string[] = [];
  if (r.extensions?.length) parts.push(r.extensions.join(" "));
  if (r.pattern) parts.push(`name ~ ${r.pattern}`);
  if (r.min_minutes != null) parts.push(`≥ ${r.min_minutes} min`);
  if (r.max_minutes != null) parts.push(`≤ ${r.max_minutes} min`);
  return parts.join(" · ");
}
