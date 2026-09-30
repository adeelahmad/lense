"use client";

import { useEffect, useId, useMemo, useState, type ReactNode } from "react";

import type { SpeakerDirectory } from "@/app/openapi-client/types.gen";
import { formatName, initialMapping, isUntimed, mappingParam, parseMapping } from "@/components/import/files";
import { MappingField, PreviewLines } from "@/components/import/mapping";
import { useTextPreview } from "@/components/import/use-import";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { Skeleton } from "@/components/ui/states";
import { plural } from "@/lib/format";

/** Import I3 (paste): paste on the left, see how it's read on the right, map speakers, import. */
export function PasteTab({
  namespace,
  namespaceControl,
  directory,
  blockReason,
  onImport,
}: {
  namespace: string | null;
  namespaceControl: ReactNode;
  directory: SpeakerDirectory | undefined;
  /** Why importing is blocked right now (no namespace, no rights), or null. */
  blockReason: string | null;
  onImport: (body: { text: string; title: string | null; speakers: string | null }) => void;
}) {
  const id = useId();
  const [text, setText] = useState("");
  const [title, setTitle] = useState("");
  const [mapping, setMapping] = useState("");
  const [touched, setTouched] = useState(false);
  const pv = useTextPreview(text);
  const labels = useMemo(() => pv.data?.speakers ?? [], [pv.data]);
  const labelKey = labels.join("\u0000");

  // New labels from the parser refresh the mapping, unless it was edited by hand.
  useEffect(() => {
    if (!touched) setMapping(initialMapping(labels));
  }, [labelKey, touched, labels]);

  const m = parseMapping(mapping, labels);
  const ready = Boolean(text.trim() && pv.data && pv.data.segments > 0 && !pv.isFetching);
  const reason =
    blockReason ??
    (!text.trim()
      ? "Paste a transcript first"
      : pv.isError
        ? "Fix the transcript first"
        : m.errors.length
          ? "Fix the speaker mapping first"
          : !ready
            ? "Reading…"
            : null);

  return (
    <div className="grid min-h-0 flex-1 md:grid-cols-2">
      <div className="flex flex-col gap-2 border-b border-border p-4 md:border-b-0 md:border-r md:px-6 md:py-[18px]">
        <label htmlFor={id} className="text-[13px] font-bold text-fg-strong">
          Paste a transcript
        </label>
        <textarea
          id={id}
          value={text}
          onChange={(e) => setText(e.target.value)}
          spellCheck={false}
          placeholder={
            "Interviewer: Thanks for making time after a night shift.\nP09: It’s fine — this is the only hour I’m properly awake."
          }
          className="min-h-[280px] w-full flex-1 resize-y rounded-sm border border-border bg-background px-3.5 py-3 font-mono text-[13px] leading-[1.6] text-fg outline-none placeholder:text-fg-muted focus:border-blue focus:shadow-[0_0_0_3px_var(--intent-surface)] md:min-h-[420px]"
        />
        <span className="text-[12px] leading-snug text-fg-muted">
          Markdown, plain text with “Name:” labels, SRT, VTT or JSON are detected automatically.
        </span>
      </div>
      <div className="flex min-w-0 flex-col gap-3.5 p-4 md:px-6 md:py-[18px]" aria-live="polite">
        {!text.trim() ? (
          <p className="text-[13.5px] text-fg-muted">
            Paste a transcript on the left to see how it will be read. Nothing is saved until you import.
          </p>
        ) : pv.isError ? (
          <p
            className="rounded-[10px] border border-red-border bg-red-surface px-3 py-2.5 text-[13px] text-fg-strong"
            role="alert"
          >
            <b className="font-bold">We couldn’t read that.</b> {(pv.error as Error).message}
          </p>
        ) : !pv.data ? (
          <div className="flex flex-col gap-3" aria-busy="true" aria-label="Reading">
            <Skeleton className="w-2/3" />
            <Skeleton className="w-full" />
            <Skeleton className="w-5/6" />
          </div>
        ) : (
          <>
            <div className="flex flex-wrap gap-2">
              <Badge tone="green" dot>
                {formatName(pv.data.format)}
                {pv.data.speakers.length ? " · labels" : ""}
              </Badge>
              <Badge>{plural(pv.data.speakers.length, "speaker")}</Badge>
              <Badge>{plural(pv.data.segments, "turn")}</Badge>
            </div>
            {isUntimed(pv.data.format) && (
              <p className="flex gap-2.5 rounded-[10px] border border-gold-border bg-gold-surface px-3 py-2.5 text-[13px] leading-[1.45] text-fg-strong">
                <span aria-hidden className="mt-1.5 size-2 shrink-0 rotate-45 bg-gold" />
                <span>
                  <b className="font-bold">No timestamps found.</b> This becomes a transcript-only recording; turn times
                  are estimated from word count.
                </span>
              </p>
            )}
            <PreviewLines preview={pv.data} mapping={mapping} variant="plain" max={3} />
            <MappingField
              value={mapping}
              onChange={(v) => {
                setMapping(v);
                setTouched(true);
              }}
              preview={pv.data}
              namespace={namespace}
              directory={directory}
            />
            <div className="grid gap-3 sm:grid-cols-[1.4fr_1fr]">
              <label className="flex flex-col gap-1.5">
                <span className="text-[13px] font-bold text-fg-strong">Title</span>
                <Input
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder={pv.data.title || "From the first line"}
                  maxLength={200}
                />
              </label>
              {namespaceControl}
            </div>
          </>
        )}
        <div className="flex-1" />
        <div className="flex flex-wrap justify-end gap-2">
          <Button
            variant="secondary"
            disabled
            disabledReason="Not available yet: audio can’t be uploaded here. Put it in a watched folder instead."
          >
            Attach audio…
          </Button>
          <Button
            variant="primary"
            disabled={Boolean(reason)}
            disabledReason={reason ?? undefined}
            onClick={() => {
              onImport({
                text,
                title: title.trim() || pv.data?.title || null,
                speakers: mappingParam(m),
              });
              setText("");
              setTitle("");
              setTouched(false);
            }}
          >
            Import
          </Button>
        </div>
      </div>
    </div>
  );
}
