import type { ImportRun, ImportRunUndone } from "@/app/openapi-client/types.gen";
import { plural } from "@/lib/format";

/** What started a run, as the list names it. */
export const RUN_KIND: Record<ImportRun["kind"], string> = {
  scan: "Folder scan",
  archive: "Archive",
  links: "Links",
  source: "From a source",
};

/** "Imported 240 assets", or how many are left once some went. */
export function runTitle(run: Pick<ImportRun, "assets" | "left" | "status">): string {
  const made = run.assets ?? 0;
  const left = run.left ?? 0;
  if (run.status === "undone") return `Undone: ${plural(made, "asset")} removed`;
  if (left < made) return `Imported ${plural(made, "asset")}, ${left} left`;
  return `Imported ${plural(made, "asset")}`;
}

/** The line under it: what made it, what it read and where it went. */
export function runDetail(run: Pick<ImportRun, "kind" | "source" | "namespace">): string {
  return [RUN_KIND[run.kind], run.source, run.namespace ? `into ${run.namespace}` : null].filter(Boolean).join(" · ");
}

/** Whether the run has anything left to undo. */
export function canUndoRun(run: Pick<ImportRun, "left">): boolean {
  return (run.left ?? 0) > 0;
}

/** The toast after an undo: how many went, and what was left because a job is working on it. */
export function undoneToast(r: Pick<ImportRunUndone, "deleted" | "busy">): { title: string; body?: string } {
  const title = r.deleted.length ? `Removed ${plural(r.deleted.length, "asset")}` : "Nothing was left to remove";
  if (!r.busy.length) return { title };
  return {
    title,
    body: `${plural(r.busy.length, "asset")} ${r.busy.length === 1 ? "is" : "are"} being processed: undo the import again once ${r.busy.length === 1 ? "it stops" : "they stop"}.`,
  };
}
