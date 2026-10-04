/** The entity graph's history, as the History tab shows it (docs/graph-history.md). */

export type Version = {
  version: number;
  at: string;
  op: string;
  actor: string;
  via: string;
  why?: string | null;
  entities?: number[];
  names?: Record<string, string>;
  changes?: number;
  tags?: string[];
};
export type Pair = { a: number; b: number; names: (string | null)[] };
export type Alias = { key: string; entity: number; name?: string | null };
export type Ent = { id: number; name?: string; type?: string };
export type Diff = {
  entities: {
    added: Ent[];
    removed: Ent[];
    changed: { id: number; name?: string; fields: Record<string, unknown[]> }[];
  };
  aliases: { added: Alias[]; removed: Alias[] };
  links: { added: Pair[]; removed: Pair[] };
  distinct: { added: Pair[]; removed: Pair[] };
};
export type Page = { head: number; versions: Version[]; next: number | null };
export type Rollback = Diff & {
  to: number;
  undo: Pick<Version, "version" | "op" | "actor" | "via" | "at">[];
  kept: number;
  done: boolean;
  version?: number;
  skipped?: { why: string }[];
};

/** What an event did, in words. */
export const OPS: Record<string, string> = {
  "entity.rename": "Renamed",
  "entity.describe": "Described",
  "entity.retype": "Changed the type of",
  "entity.hide": "Hid",
  "entity.show": "Showed",
  "entity.define": "Put on the list",
  "entity.undefine": "Took off the list",
  "entity.aliases": "Set other names of",
  "entity.learned": "Learned other names for",
  "entity.delete": "Deleted",
  "entity.merge": "Merged",
  "entity.unmerge": "Undid a merge of",
  "entity.builtin": "Made",
  "entity.found": "Found",
  "link.add": "Linked",
  "link.remove": "Unlinked",
  "distinct.add": "Marked as different",
  "mention.move": "Moved a mention between",
  "mention.remove": "Removed a mention from",
  "merge.apply": "Merged (graph change)",
  "link.apply": "Linked (graph change)",
  "merge.accept": "Accepted a merge of",
  "link.accept": "Accepted a link of",
  "merge.dismiss": "Dismissed a merge of",
  "link.dismiss": "Dismissed a link of",
  "merge.undo": "Undid a merge of",
  "link.undo": "Undid a link of",
  analysis: "Analysis found",
  "graph.rollback": "Rolled back",
  "graph.drift": "Recorded unrecorded changes to",
};
export const VIA: Record<string, string> = {
  web: "web app",
  token: "API token",
  oauth: "app",
  assistant: "assistant",
  mcp: "MCP",
  routine: "routine",
  workflow: "workflow",
  analysis: "analysis",
  cli: "command line",
  system: "Lens",
};

export const show = (v: unknown) =>
  v == null || v === "" ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v);

/** A diff as short lines: what was added, removed and changed, each with its label. */
export function diffLines(d: Diff): { label: string; items: string[] }[] {
  return [
    { label: "Added", items: d.entities.added.map((e) => `${e.name} (${e.type})`) },
    { label: "Removed", items: d.entities.removed.map((e) => `${e.name} (${e.type})`) },
    {
      label: "Changed",
      items: d.entities.changed.map(
        (c) =>
          `${c.name}: ${Object.entries(c.fields)
            .filter(([f]) => f !== "key")
            .map(([f, [a, b]]) => `${f} ${show(a)} → ${show(b)}`)
            .join(", ")}`,
      ),
    },
    { label: "Other names added", items: d.aliases.added.map((a) => `“${a.key}” → ${a.name ?? a.entity}`) },
    { label: "Other names removed", items: d.aliases.removed.map((a) => `“${a.key}” from ${a.name ?? a.entity}`) },
    { label: "Linked", items: d.links.added.map((p) => p.names.join(" ↔ ")) },
    { label: "Unlinked", items: d.links.removed.map((p) => p.names.join(" ↔ ")) },
    { label: "Marked different", items: d.distinct.added.map((p) => p.names.join(" ≠ ")) },
    { label: "No longer marked different", items: d.distinct.removed.map((p) => p.names.join(" ≠ ")) },
  ].filter((r) => r.items.length);
}
