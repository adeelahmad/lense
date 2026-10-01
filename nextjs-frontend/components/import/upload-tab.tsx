"use client";

import {
  Captions,
  FileAudio,
  FileJson,
  FileScan,
  FileText,
  FileVideo,
  FileX,
  Loader2,
  Upload,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { useState, type ReactNode } from "react";

import type { SpeakerDirectory, Upload as UploadT } from "@/app/openapi-client/types.gen";
import { extOf, formatName, isUntimed, kindOf, stemOf } from "@/components/import/files";
import { MappingField, PreviewLines } from "@/components/import/mapping";
import { chooseFiles, isFileDrag } from "@/components/import/pending";
import { pieceCount, resumeFrom, sentShare } from "@/components/import/upload-model";
import { isMedia, type Item } from "@/components/import/use-import";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { useArchive } from "@/lib/hooks/session";
import { bytes, count, plural, tc } from "@/lib/format";
import { cn } from "@/lib/utils";

const TONE = {
  ready: {
    box: "bg-surface-neutral text-fg-secondary",
    border: "border-border",
    meta: "text-fg-muted",
    glyph: "✓",
    glyphCls: "text-green",
  },
  reading: {
    box: "bg-surface-neutral text-fg-secondary",
    border: "border-border",
    meta: "text-fg-muted",
    glyph: "",
    glyphCls: "",
  },
  attention: {
    box: "bg-gold-surface text-gold-dark",
    border: "border-gold-border",
    meta: "text-gold-dark",
    glyph: "◆",
    glyphCls: "text-gold",
  },
  blocked: {
    box: "bg-red-surface text-red",
    border: "border-red-border",
    meta: "text-red-dark",
    glyph: "✕",
    glyphCls: "text-red",
  },
};

export function fileIcon(it: Pick<Item, "file" | "status" | "problem">): LucideIcon {
  const ext = extOf(it.file.name);
  const kind = kindOf(it.file.name);
  if (kind === "audio") return FileAudio;
  if (kind === "video") return FileVideo;
  if (kind === "unsupported") return FileX;
  if (it.problem?.code === "empty" && ext === ".pdf") return FileScan;
  if (ext === ".srt" || ext === ".vtt") return Captions;
  if (ext === ".json" || ext === ".jsonl") return FileJson;
  return FileText;
}

/** One line under the file name: how it was read, or what's wrong. */
export function fileMeta(it: Item): string {
  if (it.status === "reading") return `Reading… · ${bytes(it.file.size)}`;
  if (it.problem) {
    if (it.problem.code === "unsupported") return "Unsupported file type";
    if (it.problem.code === "too-large") return `${bytes(it.file.size)} — over the upload limit`;
    return it.problem.title;
  }
  if (isMedia(it.kind))
    return `${it.kind === "video" ? "Video" : "Audio"} · ${bytes(it.file.size)} · transcribed after upload`;
  const pv = it.preview;
  if (!pv) return bytes(it.file.size);
  const parts = [formatName(pv.format).replace(/ \(\.\w+\)$/, ""), plural(pv.segments, "segment")];
  if (pv.speakers.length) parts.push(plural(pv.speakers.length, "speaker"));
  if (isUntimed(pv.format)) parts.push("no timestamps");
  return parts.join(" · ");
}

function FileRow({ it, selected, onSelect }: { it: Item; selected: boolean; onSelect: () => void }) {
  const t = TONE[it.status];
  const Icon = fileIcon(it);
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        aria-current={selected || undefined}
        className={cn(
          "grid w-full grid-cols-[30px_minmax(0,1fr)_auto] items-center gap-2.5 rounded-[10px] border bg-background p-2.5 text-left transition-colors duration-fast hover:bg-surface",
          selected ? "border-blue-border bg-hl hover:bg-hl" : t.border,
        )}
      >
        <span className={cn("grid size-[30px] place-items-center rounded-sm", t.box)}>
          <Icon className="size-4" aria-hidden />
        </span>
        <span className="flex min-w-0 flex-col gap-[3px]">
          <span className="truncate text-[13px] font-semibold leading-tight text-fg">{it.file.name}</span>
          <span className={cn("truncate text-[11.5px] leading-tight", t.meta)}>{fileMeta(it)}</span>
        </span>
        <span
          className={cn("text-[12px] font-bold", t.glyphCls)}
          aria-label={
            it.status === "reading"
              ? "Reading"
              : it.status === "ready"
                ? "Ready"
                : it.status === "attention"
                  ? "Needs attention"
                  : "Can’t import"
          }
        >
          {it.status === "reading" ? (
            <Loader2 className="size-3.5 animate-spin text-fg-muted" aria-hidden />
          ) : (
            <span aria-hidden>{t.glyph}</span>
          )}
        </span>
      </button>
    </li>
  );
}

/** Drop zone + the files, sorted into ready ✓, needs attention ◆ and can't import ✕. */
export function FileList({
  items,
  selected,
  onSelect,
  onAdd,
}: {
  items: Item[];
  selected: string | null;
  onSelect: (id: string) => void;
  onAdd: (files: File[]) => void;
}) {
  const [over, setOver] = useState(false);
  const total = items.reduce((a, i) => a + i.file.size, 0);
  return (
    <div className="flex min-h-0 flex-col gap-2">
      <div
        onDragOver={(e) => {
          if (isFileDrag(e)) {
            e.preventDefault();
            setOver(true);
          }
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          if (!isFileDrag(e)) return;
          e.preventDefault();
          setOver(false);
          onAdd(Array.from(e.dataTransfer.files));
        }}
        className={cn(
          "flex items-center gap-2.5 rounded-md border-[1.5px] border-dashed border-blue-border bg-background px-3.5 py-3 text-[13px] leading-snug text-fg-secondary",
          over && "border-blue bg-blue-surface",
        )}
      >
        <Upload className="size-4 shrink-0 text-blue" aria-hidden />
        <span>
          {items.length ? "Drop more files, or " : "Drop transcripts, audio or video here, or "}
          <button
            type="button"
            className="font-semibold text-blue underline-offset-2 hover:underline"
            onClick={async () => onAdd(await chooseFiles())}
          >
            browse
          </button>
        </span>
      </div>
      {items.length > 0 && (
        <div className="flex justify-between gap-2 px-1 pb-0.5 pt-1.5 text-[12px] font-semibold text-fg-muted">
          <span>
            {plural(items.length, "file")} · {bytes(total)}
          </span>
          <span className="truncate">Nothing is saved until you import</span>
        </div>
      )}
      <ul className="flex flex-col gap-2" aria-label="Files to import">
        {items.map((it) => (
          <FileRow key={it.id} it={it} selected={selected === it.id} onSelect={() => onSelect(it.id)} />
        ))}
      </ul>
    </div>
  );
}

function Stat({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="flex flex-col gap-1 rounded-[10px] border border-border bg-surface px-3 py-2.5">
      <span className="text-[11.5px] leading-none text-fg-muted">{k}</span>
      <span className="tabular truncate text-[14px] font-bold leading-tight text-fg">{v}</span>
    </div>
  );
}

/** Library I2: what's wrong with a file, and what to do instead. */
export function ProblemCard({
  it,
  onRemove,
  onReplace,
  className,
}: {
  it: Item;
  onRemove: () => void;
  onReplace: (files: File[]) => void;
  className?: string;
}) {
  const { admin } = useArchive();
  const p = it.problem;
  if (!p) return null;
  const Icon = fileIcon(it);
  const red = it.status === "blocked";
  const kindLabel = extOf(it.file.name).slice(1).toUpperCase() || "File";
  const source = p.code === "too-large";
  const mediaType = p.code === "unsupported" && isMedia(it.kind);
  return (
    <div
      className={cn("flex flex-col gap-3.5 rounded-lg border border-border bg-background p-5", className)}
      role="group"
      aria-label={`${it.file.name}: ${p.title}`}
    >
      <div className="flex items-center gap-2.5">
        <span
          className={cn(
            "grid size-8 place-items-center rounded-[9px]",
            red ? "bg-red-surface text-red" : "bg-gold-surface text-gold-dark",
          )}
        >
          <Icon className="size-[17px]" aria-hidden />
        </span>
        <span className="flex min-w-0 flex-col gap-[3px]">
          <span className="truncate text-[14px] font-bold leading-tight text-fg">{it.file.name}</span>
          <span className="text-[12px] leading-none text-fg-muted">
            {kindLabel} · {bytes(it.file.size)}
          </span>
        </span>
      </div>
      <h2 className="text-[16px] font-bold leading-snug text-fg">{p.title}</h2>
      <p className="text-[13.5px] leading-[1.55] text-fg-secondary">{p.body}</p>
      <div className="flex flex-wrap gap-2">
        {source ? (
          admin ? (
            <Button asChild size="sm" variant="secondary">
              <Link href="/sources">Use a source</Link>
            </Button>
          ) : (
            <Button
              size="sm"
              variant="secondary"
              disabled
              disabledReason="Only admins set up sources. Ask an admin to watch a folder for this namespace."
            >
              Use a source
            </Button>
          )
        ) : (
          <Button size="sm" variant="secondary" onClick={async () => onReplace(await chooseFiles())}>
            Choose another file
          </Button>
        )}
        {mediaType && admin && (
          <Button asChild size="sm" variant="ghost">
            <Link href="/settings/uploads">Upload settings</Link>
          </Button>
        )}
        <Button size="sm" variant="ghost" onClick={onRemove}>
          {red ? "Remove" : "Skip file"}
        </Button>
      </div>
    </div>
  );
}

/** The right pane for a file that parsed: stats, first segments, speaker mapping, title and where it goes. */
export function FileDetail({
  it,
  onPatch,
  namespace,
  namespaceControl,
  pipeline,
  directory,
  audioTwin,
}: {
  it: Item;
  onPatch: (p: Partial<Item>) => void;
  namespace: string | null;
  namespaceControl: ReactNode;
  pipeline: string;
  directory: SpeakerDirectory | undefined;
  audioTwin?: string;
}) {
  const pv = it.preview;
  const Icon = fileIcon(it);
  if (!pv) return null;
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-2.5">
        <Icon className="size-[18px] text-fg-secondary" aria-hidden />
        <h2 className="min-w-0 flex-1 truncate text-[15px] font-bold text-fg">{it.file.name}</h2>
        <Badge tone="green" dot>
          Parsed
        </Badge>
      </div>
      <div className="grid grid-cols-2 gap-2.5 lg:grid-cols-4">
        <Stat k="Format" v={formatName(pv.format)} />
        <Stat k="Speakers found" v={count(pv.speakers.length)} />
        <Stat k="Segments" v={count(pv.segments)} />
        <Stat k="Span" v={`${isUntimed(pv.format) ? "~" : ""}0:00 – ${tc(pv.duration_ms)}`} />
      </div>
      <PreviewLines preview={pv} max={4} />
      <MappingField
        value={it.mapping}
        onChange={(mapping) => onPatch({ mapping, mappingTouched: true })}
        preview={pv}
        namespace={namespace}
        directory={directory}
      />
      <div className="grid gap-3 lg:grid-cols-[1.4fr_1fr_1fr]">
        <label className="flex flex-col gap-1.5">
          <span className="text-[13px] font-bold text-fg-strong">Title</span>
          <Input value={it.title} onChange={(e) => onPatch({ title: e.target.value })} maxLength={200} />
        </label>
        {namespaceControl}
        <div className="flex flex-col gap-1.5">
          <span className="text-[13px] font-bold text-fg-strong">Then run</span>
          <span
            className="flex h-10 items-center truncate rounded-sm border border-dashed border-border bg-surface px-3.5 text-[14px] text-fg-secondary"
            title="Imports run the namespace’s pipeline. Change which one in Pipelines."
          >
            {pipeline}
          </span>
        </div>
      </div>
      {isUntimed(pv.format) && (
        <p className="flex gap-2.5 rounded-[10px] border border-gold-border bg-gold-surface px-3 py-2.5 text-[13px] leading-[1.45] text-fg-strong">
          <span aria-hidden className="mt-1.5 size-2 shrink-0 rotate-45 bg-gold" />
          <span>
            <b className="font-bold">No timestamps found.</b> This becomes a transcript-only recording; turn times are
            estimated from word count.
          </span>
        </p>
      )}
      {audioTwin && (
        <p className="rounded-[10px] border border-border bg-surface px-3 py-2.5 text-[13px] leading-[1.45] text-fg-secondary">
          <b className="font-bold text-fg-strong">{audioTwin}</b> from this upload has the same name. They become two
          recordings: this transcript, and the audio, transcribed on its own.
        </p>
      )}
    </div>
  );
}

/** The right pane for audio or video: what will be sent, its title and where it goes. An earlier upload of the same
 * file that stopped part-way is carried on. */
export function MediaDetail({
  it,
  onPatch,
  namespace,
  namespaceControl,
  pipeline,
  pieceMb,
  unfinished,
}: {
  it: Item;
  onPatch: (p: Partial<Item>) => void;
  namespace: string | null;
  namespaceControl: ReactNode;
  pipeline: string;
  pieceMb: number;
  unfinished: UploadT[];
}) {
  const Icon = fileIcon(it);
  const earlier = namespace ? resumeFrom(unfinished, it.file, namespace) : null;
  const pieces = pieceCount(it.file.size, pieceMb);
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-2.5">
        <Icon className="size-[18px] text-fg-secondary" aria-hidden />
        <h2 className="min-w-0 flex-1 truncate text-[15px] font-bold text-fg">{it.file.name}</h2>
        <Badge tone="neutral" dot>
          Ready to upload
        </Badge>
      </div>
      <div className="grid grid-cols-2 gap-2.5 lg:grid-cols-3">
        <Stat k="Type" v={`${it.kind === "video" ? "Video" : "Audio"} (${extOf(it.file.name).slice(1)})`} />
        <Stat k="Size" v={bytes(it.file.size)} />
        <Stat k="Sent in" v={pieces === 1 ? "one piece" : `${count(pieces)} pieces`} />
      </div>
      <p className="text-[13px] leading-[1.5] text-fg-secondary">
        {earlier ? (
          <>
            <b className="font-bold text-fg-strong">
              An earlier upload of this file stopped at {Math.round(sentShare(earlier.offset, earlier.size) * 100)}%.
            </b>{" "}
            Importing carries on from there.
          </>
        ) : (
          <>Sent in pieces: if the connection drops, the upload carries on where it stopped.</>
        )}{" "}
        It’s transcribed once it has arrived.
      </p>
      <div className="grid gap-3 lg:grid-cols-[1.4fr_1fr_1fr]">
        <label className="flex flex-col gap-1.5">
          <span className="text-[13px] font-bold text-fg-strong">Title</span>
          <Input value={it.title} onChange={(e) => onPatch({ title: e.target.value })} maxLength={200} />
        </label>
        {namespaceControl}
        <div className="flex flex-col gap-1.5">
          <span className="text-[13px] font-bold text-fg-strong">Then run</span>
          <span
            className="flex h-10 items-center truncate rounded-sm border border-dashed border-border bg-surface px-3.5 text-[14px] text-fg-secondary"
            title="Imports run the namespace’s pipeline. Change which one in Pipelines."
          >
            {pipeline}
          </span>
        </div>
      </div>
    </div>
  );
}

/** An audio file in the upload whose name matches this transcript ("ep14.m4a" for "ep14-transcript.srt"). */
export function audioTwinOf(it: Item, items: Item[]): string | undefined {
  const norm = (s: string) =>
    stemOf(s)
      .toLowerCase()
      .replace(/[-_ ]*(transcript|captions|subtitles|subs)$/, "");
  const me = norm(it.file.name);
  return items.find((x) => x !== it && (x.kind === "audio" || x.kind === "video") && norm(x.file.name) === me)?.file
    .name;
}
