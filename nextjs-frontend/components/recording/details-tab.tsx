"use client";

import {
  Download,
  FileAudio,
  FileText,
  FileVideo,
  Film,
  GitCommitHorizontal,
  Workflow,
  type LucideIcon,
} from "lucide-react";
import type { ReactNode } from "react";

import { useRec } from "@/components/recording/context";
import { sourceLabel, transcriptOrigin } from "@/components/recording/labels";
import { Button } from "@/components/ui/button";
import { absolute, bytes, count, plural, tc } from "@/lib/format";

type Row = [string, ReactNode, boolean?];

/** Details tab (Recording Pages §2): file, streams, provenance and processing — what the recording row records. */
export function DetailsTab() {
  const { rec, model, paged } = useRec();
  const video = model.media.kind === "video";
  const src = sourceLabel(rec);
  // Pasted transcripts have no file name ("paste:<hash>" is an internal key); uploads drop their "upload:" prefix.
  const pasted = !rec.remote?.path && (rec.path ?? "").startsWith("paste:");
  const name = pasted
    ? null
    : (rec.remote?.path ?? rec.path ?? "")
        .split(/[\\/]/)
        .pop()
        ?.replace(/^upload:/, "") || null;
  const file: Row[] = [
    ["Name", name, true],
    [
      "Kind",
      model.media.kind === "document"
        ? "Document"
        : model.media.kind === "image"
          ? "Image"
          : video
            ? "Video"
            : model.audio
              ? "Audio"
              : "Transcript only (no audio)",
    ],
    paged ? ["Pages", count(model.pages.length)] : ["Duration", tc(model.durationMs)],
    ["Size", rec.size ? bytes(rec.size) : null],
    paged
      ? [
          "Text",
          [
            plural(model.segments.length, "block"),
            plural(model.pages.filter((p) => p.text === "ocr").length, "page") + " read by OCR",
          ]
            .filter(Boolean)
            .join(" · "),
        ]
      : [
          "Transcript",
          [plural(model.segments.length, "line"), transcriptOrigin(rec.engine)].filter(Boolean).join(" · "),
        ],
    ["Language", rec.language && !["none", "nospeech"].includes(rec.language) ? rec.language.toUpperCase() : null],
  ];
  const media: Row[] = [
    [
      paged ? "Page size" : "Frame size",
      model.media.width && model.media.height ? `${model.media.width}×${model.media.height}` : null,
    ],
    ["Frame rate", model.media.fps ? `${Math.round(model.media.fps * 100) / 100} fps` : null],
    [
      "Channels",
      rec.channels ? `${rec.channels}${rec.channels === 1 ? " (mono)" : rec.channels === 2 ? " (stereo)" : ""}` : null,
    ],
    ["Shots", video ? count(model.shots.length) : null],
    ["Text on screen", video ? plural(model.screenText.length, "line") : null],
    ["People on screen", video ? (model.facesMode === "off" ? "face detection off" : count(model.faces.length)) : null],
  ];
  const provenance: Row[] = [
    [
      "Source",
      src ? <span title={src.title}>{src.remote || !src.file ? src.text : (rec.path ?? src.text)}</span> : null,
      src?.file,
    ],
    ["Imported", rec.created_at ? absolute(rec.created_at) : null],
    ["Recorded", rec.recorded_at ? absolute(rec.recorded_at) : null],
    ["Fingerprint", rec.fingerprint ?? null, true],
  ];
  const processing: Row[] = [
    [
      "Transcribe",
      rec.transcribed_at ? `${transcriptOrigin(rec.engine) ?? "done"} · ${absolute(rec.transcribed_at)}` : "not run",
    ],
    [
      "Diarize",
      rec.diarized_at
        ? `${rec.diarizer === "labels" ? "speakers from the transcript" : (rec.diarizer ?? "done")} · ${absolute(rec.diarized_at)}`
        : "not run",
    ],
    [
      "Analyze",
      rec.analyzed_at
        ? `${plural(model.chapters.length, "chapter")} · ${plural(model.entities.length, "entity", "entities")} · ${absolute(rec.analyzed_at)}`
        : "not run",
    ],
    ["Summarize", rec.summarized_at ? absolute(rec.summarized_at) : "not run"],
    [
      "Report",
      rec.report_url ? (
        <a
          href={rec.report_url}
          target="_blank"
          rel="noreferrer"
          className="font-semibold text-fg-accent hover:underline"
        >
          Open the report
        </a>
      ) : (
        "not built"
      ),
    ],
  ];
  return (
    <>
      <div className="flex items-center gap-2">
        <p className="flex-1 text-[12.5px] leading-snug text-fg-muted">
          From the recording&apos;s own record. Codec and loudness details aren&apos;t measured yet.
        </p>
        {model.audio && (
          <Button asChild variant="secondary" size="sm" icon={<Download />}>
            <a href={model.audio} download={name ?? undefined}>
              Original
            </a>
          </Button>
        )}
      </div>
      <Group icon={video ? FileVideo : model.audio ? FileAudio : FileText} title="File" rows={file} />
      {(video || rec.channels) && <Group icon={Film} title={video ? "Video and audio" : "Audio"} rows={media} />}
      <Group icon={GitCommitHorizontal} title="Provenance" rows={provenance} />
      <Group icon={Workflow} title="Processing" rows={processing} />
    </>
  );
}

function Group({ icon: Icon, title, rows }: { icon: LucideIcon; title: string; rows: Row[] }) {
  const shown = rows.filter(([, v]) => v != null && v !== "");
  if (!shown.length) return null;
  return (
    <section className="flex flex-col gap-0.5 rounded-md border border-border px-4 py-3.5" aria-label={title}>
      <h3 className="flex items-center gap-2 pb-2 text-[13px] font-bold leading-none">
        <Icon aria-hidden className="size-[15px] text-fg-secondary" />
        {title}
      </h3>
      <dl className="m-0">
        {shown.map(([k, v, mono]) => (
          <div
            key={k}
            className="grid grid-cols-[120px_minmax(0,1fr)] gap-2.5 border-t border-border py-1.5 text-[13px] leading-[1.4]"
          >
            <dt className="text-fg-muted">{k}</dt>
            <dd className={mono ? "m-0 break-words font-mono text-[12px] text-fg" : "tabular m-0 break-words text-fg"}>
              {v}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
