"use client";

import { useEffect, useState } from "react";

import type { EvidencePdfData } from "@/app/openapi-client/types.gen";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Checkbox } from "@/components/ui/field";

export type EvidencePart = NonNullable<NonNullable<EvidencePdfData["query"]>["include"]>[number];

/** The parts of an evidence PDF, in their order there (the API's `include`), with what each holds. */
export const EVIDENCE_PARTS: [EvidencePart, string, string][] = [
  ["file", "Its own file", "A document’s pages, an image, or audio and video with the transcript"],
  ["details", "Details", "What it is, dates, length, language, content type, where it came from"],
  ["metadata", "Metadata", "Its description, rights, creators, Dublin Core terms, custom fields"],
  ["tags", "Tags", "Its tags"],
  ["topics", "Topics", "Accepted and suggested topics"],
  ["summary", "Summary", "Written by a model: key points, follow-ups, tone, importance"],
  ["analysis", "Analysis", "What its pipeline’s model steps saved"],
  ["sections", "Sections", "The sections analysis split it into"],
  ["speakers", "Speakers", "Who spoke, for how long, how each was recognised"],
  ["emotion", "Emotion and sounds", "Overall and by speaker, marked on each transcript line"],
  ["entities", "Entities", "People, organisations, places and other names"],
  ["keywords", "Keywords", "Its keywords"],
  ["visual", "What’s seen", "Faces, objects, descriptions, text on screen"],
  ["places", "Places and dates", "Found in it, and where a photo was taken"],
  ["notes", "Notes, highlights and comments", "The ones people shared on it"],
];

/** What goes in when nothing is picked: everything Lens found, not people's notes (the API's default too). */
export const DEFAULT_PARTS: EvidencePart[] = EVIDENCE_PARTS.map(([k]) => k).filter((k) => k !== "notes");

const KEY = "lens.evidence-parts";

function remembered(): EvidencePart[] {
  try {
    const known = new Set<string>(EVIDENCE_PARTS.map(([k]) => k));
    const saved = JSON.parse(localStorage.getItem(KEY) ?? "null") as string[] | null;
    const parts = (saved ?? []).filter((k): k is EvidencePart => known.has(k));
    return parts.length ? parts : DEFAULT_PARTS;
  } catch {
    return DEFAULT_PARTS;
  }
}

/** Pick what goes in an evidence PDF, then download it (Enter downloads). Starts from the last picks made here. */
export function EvidencePartsDialog({
  open,
  onOpenChange,
  onDownload,
  what,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDownload: (include: EvidencePart[]) => void;
  /** What it is of ("“Weekly sync”", "the 12 resources in Supplier dispute"), for the description. */
  what: string;
}) {
  const [picked, setPicked] = useState<Set<EvidencePart>>(() => new Set(DEFAULT_PARTS));
  useEffect(() => {
    if (open) setPicked(new Set(remembered()));
  }, [open]);
  const all = picked.size === EVIDENCE_PARTS.length;
  const toggle = (k: EvidencePart, on: boolean) =>
    setPicked((p) => {
      const next = new Set(p);
      if (on) next.add(k);
      else next.delete(k);
      return next;
    });
  const download = () => {
    const include = EVIDENCE_PARTS.map(([k]) => k).filter((k) => picked.has(k));
    try {
      localStorage.setItem(KEY, JSON.stringify(include));
    } catch {
      /* the next time starts from everything Lens found */
    }
    onOpenChange(false);
    onDownload(include);
  };
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="What goes in the evidence PDF"
      description={`Pick the parts of ${what}. Each resource’s file comes first, then what Lens found about it, then a page with every file’s SHA-256.`}
      wide
      actions={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="primary"
            data-autofocus
            disabled={picked.size === 0}
            disabledReason="Pick at least one part"
            onClick={download}
          >
            Download PDF
          </Button>
        </>
      }
    >
      <Checkbox
        checked={all ? true : picked.size ? "indeterminate" : false}
        onCheckedChange={(on) => setPicked(new Set(on ? EVIDENCE_PARTS.map(([k]) => k) : []))}
        label="Everything"
        className="font-bold"
      />
      <div className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
        {EVIDENCE_PARTS.map(([k, label, hint]) => (
          <div key={k} className="flex flex-col gap-0.5">
            <Checkbox checked={picked.has(k)} onCheckedChange={(on) => toggle(k, on)} label={label} />
            <span className="pl-[28px] text-[12.5px] leading-snug text-fg-secondary">{hint}</span>
          </div>
        ))}
      </div>
    </Dialog>
  );
}
