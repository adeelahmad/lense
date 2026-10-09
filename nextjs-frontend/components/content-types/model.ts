/** Content types as the app shows them. */

/** What Lens can find a file holds (the API's ContentRules.forms). */
export type Form = "chat" | "records";

export type Rules = {
  extensions?: string[];
  pattern?: string;
  min_minutes?: number;
  max_minutes?: number;
  /** What the file was found to hold, e.g. ["chat"]: set by Lens, kept when the type is edited. */
  forms?: Form[];
};

const FORM_NAME: Record<Form, string> = { chat: "holds a chat", records: "holds records" };

/** "ext .srt .vtt · name ~ podcast|episode · ≥ 10 min" */
export function rulesText(r?: Rules | null): string {
  if (!r) return "";
  const parts: string[] = [];
  if (r.extensions?.length) parts.push(r.extensions.join(" "));
  if (r.pattern) parts.push(`name ~ ${r.pattern}`);
  if (r.min_minutes != null) parts.push(`≥ ${r.min_minutes} min`);
  if (r.max_minutes != null) parts.push(`≤ ${r.max_minutes} min`);
  for (const f of r.forms ?? []) parts.push(FORM_NAME[f]);
  return parts.join(" · ");
}
