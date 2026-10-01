"use client";

import {
  Captions,
  Download,
  Ellipsis,
  FileAudio,
  FileText,
  FileVideo,
  Globe,
  Image as ImageIcon,
  Languages,
  ListTree,
  Paperclip,
  Pencil,
  Rows3,
  SlidersHorizontal,
  Trash2,
  Upload,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import type { PrimaryFile, ResourceFile } from "@/app/openapi-client/types.gen";
import { FieldValuesPanel } from "@/components/fields/fields-ui";
import { useNamespaceFields } from "@/components/fields/use-fields";
import { chooseFiles } from "@/components/import/pending";
import { usePlayerApi, usePlayerState } from "@/components/player/media";
import { useRec } from "@/components/recording/context";
import {
  READ,
  ROLES,
  addedBy,
  deleteQuestion,
  detailChanges,
  fileMeta,
  fileTitle,
  guessRole,
  languageProblem,
  lineTime,
  sizeProblem,
  typeProblem,
  type FileRole,
} from "@/components/recording/files-model";
import { useFileActions, useFileLines, useFiles } from "@/components/recording/hooks";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select, Textarea } from "@/components/ui/field";
import { Menu, MenuContent, MenuItem, MenuSeparator, MenuTrigger } from "@/components/ui/menu";
import { Label } from "@/components/ui/panel";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { absolute, bytes, relative } from "@/lib/format";
import { needRole } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

const ICON: Record<FileRole, ReactNode> = {
  transcript: <FileText />,
  captions: <Captions />,
  translation: <Languages />,
  index: <ListTree />,
  thumbnail: <ImageIcon />,
  attachment: <Paperclip />,
};
const chip =
  "inline-flex h-[22px] shrink-0 items-center rounded-pill border border-blue-border bg-blue-surface px-2 font-sans text-[12px] font-semibold tabular-nums text-blue-dark hover:bg-blue hover:text-white";

/**
 * Files tab: the resource's primary file (its audio or video) and its supplementary files. Transcripts, captions,
 * translations and indexes are read into lines that search finds and that play from their times; every file
 * downloads. Editors add, describe and delete files; a public resource opens them with its parts (attachments never).
 */
export function FilesTab() {
  const { id, ns, canEdit, fileFocus } = useRec();
  const files = useFiles(id);
  const [adding, setAdding] = useState(false);
  const list = files.data?.files ?? [];
  return (
    <>
      <div className="flex items-start gap-3">
        <p className="flex-1 text-[13px] leading-snug text-fg-secondary">
          Transcripts, captions, translations and indexes added here are searchable.
        </p>
        <Button
          size="sm"
          variant="secondary"
          icon={<Upload />}
          disabled={!canEdit || !files.data}
          disabledReason={canEdit ? undefined : needRole("editor", ns)}
          onClick={() => setAdding(true)}
        >
          Add file
        </Button>
      </div>
      {files.isLoading ? (
        <div className="flex flex-col gap-3" aria-busy>
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
        </div>
      ) : files.isError ? (
        <EmptyState tone="error" icon={<Paperclip />} title="Couldn’t load the files">
          {files.error.message}
        </EmptyState>
      ) : (
        <>
          {files.data?.primary && <Primary p={files.data.primary} />}
          {list.length === 0 ? (
            <EmptyState icon={<Paperclip />} title="No other files yet" className="py-6">
              {canEdit
                ? "Add a transcript, captions, a translation, an index, a thumbnail or an attachment."
                : "Editors can add transcripts, captions, translations, indexes and attachments here."}
            </EmptyState>
          ) : (
            <section aria-label="Supplementary files" className="flex flex-col gap-1">
              <Label as="h3">Supplementary files</Label>
              <ul className="flex flex-col">
                {list.map((f) => (
                  <FileRow key={f.id} f={f} focus={fileFocus?.file === f.id ? (fileFocus.line ?? -1) : null} />
                ))}
              </ul>
            </section>
          )}
        </>
      )}
      {files.data && <AddFileDialog open={adding} onOpenChange={setAdding} maxMb={files.data.max_mb} />}
    </>
  );
}

function Primary({ p }: { p: PrimaryFile }) {
  const Icon = p.kind === "video" ? FileVideo : FileAudio;
  return (
    <section
      aria-label="Primary file"
      className="flex items-center gap-2.5 rounded-lg border border-border bg-surface p-3"
    >
      <Icon className="size-[18px] shrink-0 text-fg-secondary" aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="truncate text-[14px] font-semibold text-fg">
          {p.name ?? (p.kind === "video" ? "Video" : "Audio")}
        </p>
        <p className="truncate text-[12px] text-fg-muted">
          {["Primary", p.kind === "video" ? "Video" : "Audio", p.size != null ? bytes(p.size) : null]
            .filter(Boolean)
            .join(" · ")}
        </p>
      </div>
      <Button asChild size="sm" variant="ghost">
        <a href={p.download} download={p.name ?? true} aria-label={`Download ${p.name ?? "the media"}`}>
          <Download />
          Download
        </a>
      </Button>
    </section>
  );
}

/** One file: what it is, who added it, its lines (when it has them), and what you may do with it. */
function FileRow({ f, focus }: { f: ResourceFile; focus: number | null }) {
  const { id, ns, canEdit } = useRec();
  const { remove } = useFileActions(id);
  const [open, setOpen] = useState(focus != null && Boolean(f.lines));
  const [editing, setEditing] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [describing, setDescribing] = useState(false);
  const fieldDefs = useNamespaceFields(ns);
  const fileFields = (fieldDefs.data ?? []).some((x) => x.target === "file");
  const why = canEdit ? undefined : needRole("editor", ns);
  return (
    <li data-file={f.id} className="flex flex-col gap-1.5 border-t border-border py-3 first:border-t-0">
      <div className="flex min-w-0 items-center gap-2.5">
        <span
          aria-hidden
          className="grid size-8 shrink-0 place-items-center rounded-md bg-surface-neutral text-fg-secondary [&_svg]:size-4"
        >
          {ICON[f.role]}
        </span>
        <div className="min-w-0 flex-1">
          <a
            href={f.download}
            download={f.name}
            className="block truncate text-[14px] font-semibold text-fg hover:text-fg-accent hover:underline"
          >
            {fileTitle(f)}
          </a>
          <p className="truncate text-[12px] text-fg-muted">{fileMeta(f)}</p>
        </div>
        {f.public && (
          <Badge tone="green" className="shrink-0">
            <Globe className="size-3" aria-hidden />
            Public
          </Badge>
        )}
        <Menu>
          <MenuTrigger asChild>
            <button
              type="button"
              aria-label={`Actions for ${fileTitle(f)}`}
              className="grid size-7 shrink-0 place-items-center rounded-full text-fg-secondary hover:bg-surface-neutral"
            >
              <Ellipsis className="size-4" />
            </button>
          </MenuTrigger>
          <MenuContent align="end" className="min-w-[220px]">
            <MenuItem asChild>
              <a href={f.download} download={f.name}>
                <Download />
                Download
              </a>
            </MenuItem>
            {f.lines ? (
              <MenuItem icon={<Rows3 />} onSelect={() => setOpen((o) => !o)}>
                {open ? "Hide its lines" : "Show its lines"}
              </MenuItem>
            ) : null}
            {fileFields && (
              <MenuItem icon={<SlidersHorizontal />} onSelect={() => setDescribing(true)}>
                Fields…
              </MenuItem>
            )}
            <MenuSeparator />
            <MenuItem icon={<Pencil />} disabled={!canEdit} onSelect={() => setEditing(true)}>
              {canEdit ? "Edit details…" : why}
            </MenuItem>
            <MenuItem icon={<Trash2 />} danger disabled={!canEdit} onSelect={() => setConfirm(true)}>
              {canEdit ? "Delete…" : why}
            </MenuItem>
          </MenuContent>
        </Menu>
      </div>
      {(f.label || f.description) && (
        <div className="flex flex-col gap-0.5 pl-[42px]">
          {f.label && <p className="truncate text-[12px] text-fg-muted">{f.name}</p>}
          {f.description && (
            <p className="whitespace-pre-wrap break-words text-[13px] leading-snug text-fg-secondary">
              {f.description}
            </p>
          )}
        </div>
      )}
      <p className="pl-[42px] text-[12px] text-fg-muted">
        {addedBy(f)}
        {f.created_at && (
          <>
            {addedBy(f) ? " · " : ""}
            <time title={absolute(f.created_at)}>{relative(f.created_at)}</time>
          </>
        )}
      </p>
      {open && f.lines ? <Lines f={f} focus={focus != null && focus >= 0 ? focus : null} /> : null}
      {confirm && (
        <div className="flex flex-wrap items-center gap-1.5" role="alert">
          <span className="flex-1 text-[12.5px] text-fg-strong">{deleteQuestion(f)}</span>
          <Button size="sm" variant="ghost" onClick={() => setConfirm(false)}>
            Keep
          </Button>
          <Button size="sm" variant="danger" disabled={remove.isPending} onClick={() => remove.mutate(f.id)}>
            Delete
          </Button>
        </div>
      )}
      {editing && <EditFileDialog f={f} onClose={() => setEditing(false)} />}
      {describing && (
        <Dialog
          open
          onOpenChange={(o) => !o && setDescribing(false)}
          title={`Fields of ${fileTitle(f)}`}
          description="The custom fields that describe this file."
        >
          <FieldValuesPanel source={{ kind: "file", rid: id, fid: f.id }} title="Fields" />
        </Dialog>
      )}
    </li>
  );
}

/** The lines read from a file, a page at a time; timed ones play from their time. `focus` is a line to show. */
function Lines({ f, focus }: { f: ResourceFile; focus: number | null }) {
  const { id } = useRec();
  const api = usePlayerApi();
  const { hasMedia } = usePlayerState();
  const q = useFileLines(id, f.id);
  const lines = q.data?.pages.flatMap((p) => p.lines) ?? [];
  const total = q.data?.pages[0]?.total ?? f.lines ?? 0;
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = q;
  // A line further down than the first page: load until it's there, then show it.
  const reached = focus == null || lines.some((l) => l.idx === focus);
  useEffect(() => {
    if (!reached && hasNextPage && !isFetchingNextPage) void fetchNextPage();
  }, [reached, hasNextPage, isFetchingNextPage, fetchNextPage]);
  useEffect(() => {
    if (focus == null || !reached) return;
    document.querySelector(`[data-file="${f.id}"] [data-line="${focus}"]`)?.scrollIntoView({ block: "center" });
  }, [focus, reached, f.id]);
  if (q.isLoading) return <Skeleton className="ml-[42px] h-20" />;
  if (q.isError)
    return <p className="pl-[42px] text-[13px] text-red-dark">Couldn’t load its lines: {q.error.message}</p>;
  return (
    <div className="ml-[42px] flex flex-col gap-1.5">
      <ol
        aria-label={`Lines of ${fileTitle(f)}`}
        className="flex flex-col gap-0.5 rounded-md border border-border bg-surface p-1.5"
      >
        {lines.map((l) => {
          const time = lineTime(l);
          return (
            <li
              key={l.idx}
              data-line={l.idx}
              className={cn("flex gap-2 rounded-sm px-1.5 py-1 text-[13px] leading-snug", l.idx === focus && "bg-hl")}
            >
              {time &&
                (hasMedia ? (
                  <button
                    type="button"
                    className={chip}
                    aria-label={`Play from ${time}`}
                    onClick={() => api.seek(l.t0 ?? 0, { manual: true })}
                  >
                    {time}
                  </button>
                ) : (
                  <span className="tabular shrink-0 pt-px text-[12px] font-semibold text-fg-secondary">{time}</span>
                ))}
              <span className="min-w-0 flex-1 break-words text-fg">
                {f.role === "index" ? (
                  <>
                    {l.title && <b className="font-semibold">{l.title}</b>}
                    {l.synopsis && <span className="block text-fg-secondary">{l.synopsis}</span>}
                    {l.keywords?.length ? (
                      <span className="block text-[12px] text-fg-muted">{l.keywords.join(" · ")}</span>
                    ) : null}
                  </>
                ) : (
                  <>
                    {l.speaker && <b className="font-semibold">{l.speaker}: </b>}
                    {l.text}
                  </>
                )}
              </span>
            </li>
          );
        })}
      </ol>
      {hasNextPage && (
        <Button
          size="xs"
          variant="ghost"
          className="self-start"
          disabled={isFetchingNextPage}
          onClick={() => void fetchNextPage()}
        >
          {isFetchingNextPage ? "Loading…" : `Show more (${lines.length} of ${total})`}
        </Button>
      )}
    </div>
  );
}

/** Add a file: choose it, say what it is (guessed from its name), and its language and label if you like. */
function AddFileDialog({
  open,
  onOpenChange,
  maxMb,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  maxMb: number;
}) {
  const { id } = useRec();
  const { add } = useFileActions(id);
  const [file, setFile] = useState<File | null>(null);
  const [role, setRole] = useState<FileRole>("transcript");
  const [language, setLanguage] = useState("");
  const [label, setLabel] = useState("");
  const info = ROLES.find((r) => r.value === role)!;
  const problem = file ? (sizeProblem(file.size, maxMb) ?? typeProblem(role, file.name)) : null;
  const langProblem = languageProblem(language);
  const reset = () => {
    setFile(null);
    setRole("transcript");
    setLanguage("");
    setLabel("");
  };
  const pick = async () => {
    const [f] = await chooseFiles();
    if (!f) return;
    setFile(f);
    setRole(guessRole(f.name));
  };
  const send = () => {
    if (!file || problem || langProblem) return;
    add.mutate(
      { file, role, language: language.trim() || null, label: label.trim() || null },
      {
        onSuccess: () => {
          reset();
          onOpenChange(false);
        },
      },
    );
  };
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) reset();
        onOpenChange(o);
      }}
      title="Add a file"
      description={`Up to ${maxMb} MB. Editors can change or delete it later.`}
      actions={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={!file || Boolean(problem) || Boolean(langProblem) || add.isPending}
            disabledReason={!file ? "Choose a file first" : (problem ?? langProblem ?? undefined)}
            onClick={send}
          >
            {add.isPending ? "Adding…" : "Add file"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <div className="flex flex-col gap-1.5">
          {file ? (
            <div className="flex items-center gap-2.5 rounded-[10px] border border-border bg-surface px-3.5 py-3">
              <span aria-hidden className="text-fg-secondary [&_svg]:size-[18px]">
                {ICON[role]}
              </span>
              <span className="min-w-0 flex-1 truncate text-[14px] font-semibold text-fg">{file.name}</span>
              <span className="tabular shrink-0 text-[12.5px] text-fg-muted">{bytes(file.size)}</span>
            </div>
          ) : null}
          <Button variant="secondary" icon={<Paperclip />} className="self-start" onClick={() => void pick()}>
            {file ? "Another file…" : "Choose a file…"}
          </Button>
          {problem && (
            <p role="alert" className="text-[12.5px] leading-snug text-red-dark">
              {problem}
            </p>
          )}
        </div>
        <Field label="What it is" hint={`${info.hint}${info.accepts ? ` ${info.accepts.join(" ")}.` : ""}`}>
          {(ids) => (
            <Select
              id={ids.id}
              aria-describedby={ids.describedBy}
              value={role}
              onChange={(e) => setRole(e.target.value as FileRole)}
              options={ROLES.map((r) => ({ value: r.value, label: r.label }))}
            />
          )}
        </Field>
        <div className="grid gap-4 sm:grid-cols-[140px_minmax(0,1fr)]">
          <Field label="Language" optional error={langProblem} hint={langProblem ? undefined : "en, pt-BR"}>
            {(ids) => (
              <Input
                id={ids.id}
                aria-describedby={ids.describedBy}
                invalid={ids.invalid}
                value={language}
                maxLength={40}
                onChange={(e) => setLanguage(e.target.value)}
              />
            )}
          </Field>
          <Field label="Label" optional hint="Shown instead of its file name">
            {(ids) => (
              <Input
                id={ids.id}
                aria-describedby={ids.describedBy}
                value={label}
                maxLength={200}
                onChange={(e) => setLabel(e.target.value)}
              />
            )}
          </Field>
        </div>
        {READ.includes(role) && (
          <p className="text-[12.5px] leading-snug text-fg-muted">
            Lens reads it into lines when it’s added; a file it can’t read isn’t added.
          </p>
        )}
      </div>
    </Dialog>
  );
}

/** Change a file's role, language, label and description. */
function EditFileDialog({ f, onClose }: { f: ResourceFile; onClose: () => void }) {
  const { id } = useRec();
  const { update } = useFileActions(id);
  const [role, setRole] = useState<FileRole>(f.role);
  const [label, setLabel] = useState(f.label ?? "");
  const [language, setLanguage] = useState(f.language ?? "");
  const [description, setDescription] = useState(f.description ?? "");
  const changes = detailChanges(f, { role, label, language, description });
  const problem = typeProblem(role, f.name);
  const langProblem = languageProblem(language);
  const empty = Object.keys(changes).length === 0;
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`Edit ${fileTitle(f)}`}
      actions={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={empty || Boolean(problem) || Boolean(langProblem) || update.isPending}
            disabledReason={problem ?? langProblem ?? (empty ? "Nothing has changed" : undefined)}
            onClick={() => update.mutate({ fid: f.id, body: changes }, { onSuccess: onClose })}
          >
            {update.isPending ? "Saving…" : "Save"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Field label="What it is" error={problem} hint={problem ? undefined : "A new role reads the file again."}>
          {(ids) => (
            <Select
              id={ids.id}
              aria-describedby={ids.describedBy}
              invalid={ids.invalid}
              value={role}
              onChange={(e) => setRole(e.target.value as FileRole)}
              options={ROLES.map((r) => ({ value: r.value, label: r.label }))}
            />
          )}
        </Field>
        <div className="grid gap-4 sm:grid-cols-[140px_minmax(0,1fr)]">
          <Field label="Language" optional error={langProblem}>
            {(ids) => (
              <Input
                id={ids.id}
                aria-describedby={ids.describedBy}
                invalid={ids.invalid}
                value={language}
                maxLength={40}
                onChange={(e) => setLanguage(e.target.value)}
              />
            )}
          </Field>
          <Field label="Label" optional>
            {(ids) => <Input id={ids.id} value={label} maxLength={200} onChange={(e) => setLabel(e.target.value)} />}
          </Field>
        </div>
        <Field label="Description" optional>
          {(ids) => (
            <Textarea
              id={ids.id}
              rows={3}
              maxLength={2000}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          )}
        </Field>
      </div>
    </Dialog>
  );
}
