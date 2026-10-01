"use client";

import type { UploadLimits } from "@/app/openapi-client/types.gen";
import type { FileKind } from "@/components/import/files";
import { canBeTranscript, conversionProblem, DEFAULT_LIMITS, uploadKindName } from "@/components/import/files";
import type { Item } from "@/components/import/use-import";
import { Segmented } from "@/components/ui/tabs";

/**
 * A PDF, Word, text or Markdown file is imported as a document (its pages, read; scans by OCR) unless someone chooses a
 * transcript (its text only). Where the server can't make a document of it, it's a transcript, and this says why.
 */
export function ImportAs({
  it,
  onKind,
  limits = DEFAULT_LIMITS,
}: {
  it: Item;
  onKind: (kind: FileKind) => void;
  limits?: UploadLimits;
}) {
  if (!canBeTranscript(it.file.name)) return null;
  const cannot = conversionProblem(it.file.name, limits);
  if (cannot)
    return (
      <p className="text-[12.5px] leading-[1.45] text-fg-muted">
        Imported as a transcript: this server can’t keep {uploadKindName("document", it.file.name).toLowerCase()}s as
        documents. {cannot}
      </p>
    );
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
