import type { Collection } from "@/app/openapi-client/types.gen";

export type CollectionFilter = {
  namespaces?: string[];
  speakers?: number[];
  entities?: number[];
  from?: string;
  to?: string;
  q?: string;
  media?: string;
  status?: string;
};

/** "Filter · namespace research-interviews · “handover” · updates" or "Fixed list · yours". */
export function describeCollection(
  c: Pick<Collection, "kind" | "filter" | "shared" | "account">,
  me: number | undefined,
  names: {
    speakers?: Map<number, string>;
    entities?: Map<number, string>;
  } = {},
): string {
  if (c.kind === "fixed")
    return ["Fixed list", c.account === me ? (c.shared ? "yours, shared" : "yours") : "shared with you"].join(" · ");
  const f = (c.filter ?? {}) as CollectionFilter;
  const parts = ["Filter"];
  if (f.namespaces?.length) parts.push(`namespace ${f.namespaces.join(", ")}`);
  if (f.q) parts.push(`“${f.q}”`);
  if (f.speakers?.length)
    parts.push(`speaker ${f.speakers.map((id) => names.speakers?.get(id) ?? `#${id}`).join(", ")}`);
  if (f.entities?.length)
    parts.push(`entity ${f.entities.map((id) => names.entities?.get(id) ?? `#${id}`).join(", ")}`);
  if (f.from || f.to) parts.push([f.from, f.to].filter(Boolean).join(" – "));
  if (f.media) parts.push(f.media);
  if (f.status) parts.push(`status ${f.status}`);
  parts.push("updates");
  if (c.account !== me) parts.push("shared with you");
  return parts.join(" · ");
}
