/**
 * The More menu's spec items for an asset (docs/notes.md, Specs from transcripts): open the note once there is one,
 * and write it, or write it again.
 */
import type { Spec } from "@/app/openapi-client/types.gen";

export type SpecAction = {
  /** The spec's note, once one is written. */
  open?: string;
  write: string;
  disabled: boolean;
};

export function specAction(spec: Pick<Spec, "status" | "note"> | undefined, canProcess: boolean): SpecAction {
  const writing = spec?.status === "writing";
  const open = spec?.note != null ? `/notes/${spec.note}` : undefined;
  return {
    open,
    write: writing ? "Writing the spec…" : open ? "Write the spec again" : "Write a spec",
    disabled: !canProcess || writing,
  };
}
