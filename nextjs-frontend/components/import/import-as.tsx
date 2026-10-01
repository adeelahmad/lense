"use client";

import type { FileKind } from "@/components/import/files";
import { canBeTranscript } from "@/components/import/files";
import type { Item } from "@/components/import/use-import";
import { Segmented } from "@/components/ui/tabs";

/** A PDF is imported as a document (its pages, read; scans by OCR) unless someone chooses a transcript (its text only). */
export function ImportAs({ it, onKind }: { it: Item; onKind: (kind: FileKind) => void }) {
  if (!canBeTranscript(it.file.name)) return null;
  const doc = it.kind !== "transcript";
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
      <span className="text-[13px] font-bold text-fg-strong">Import as</span>
      <Segmented
        label="Import as"
        items={[
          { value: "document", label: "Document" },
          { value: "transcript", label: "Transcript" },
        ]}
        value={doc ? "document" : "transcript"}
        onChange={(v) => onKind(v as FileKind)}
      />
      <span className="text-[12.5px] text-fg-muted">
        {doc ? "Its pages, to look at and search." : "Only its text, as a transcript without media."}
      </span>
    </div>
  );
}
