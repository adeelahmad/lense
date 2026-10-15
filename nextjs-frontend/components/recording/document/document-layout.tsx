"use client";

import {
  ChevronLeft,
  ChevronRight,
  Eye,
  FileText,
  ImageOff,
  Loader2,
  Minus,
  Pencil,
  Plus,
  ScanSearch,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";

import { homeText } from "@/components/library/collections-model";
import { usePlayerApi } from "@/components/player/media";
import { RevealCover, useBlur } from "@/components/nsfw/nsfw";
import { useMediaQuery } from "@/components/player/use-media-query";
import { highlightRanges, highlightRuns, openThreads } from "@/components/recording/comments-model";
import { useRec, type PanelTab } from "@/components/recording/context";
import { HighlightMark } from "@/components/recording/highlight-mark";
import {
  blocksByPage,
  clampPage,
  facesOn,
  marksOn,
  pageAtOffset,
  pageNumber,
  pagesSummary,
  pageStart,
  textNote,
  zoomStep,
  type FaceMark,
  type Mark,
} from "@/components/recording/document/model";
import { Banners, HeaderActions, RecordingHeader } from "@/components/recording/header";
import {
  useComments,
  useHighlights,
  useNotes,
  useRecordingActions,
  useVisualNotes,
} from "@/components/recording/hooks";
import { descriptionOf, segmentAt, type Box, type PageInfo } from "@/components/recording/model";
import { boxesOnPage, objectName } from "@/components/recording/objects-model";
import { ObjectsTab } from "@/components/recording/objects-tab";
import { MORE_TABS, PanelBody, PanelScroll, PanelTabs, type TabDef } from "@/components/recording/side-panel";
import { FindBar } from "@/components/recording/find-bar";
import { SelectionToolbar } from "@/components/recording/selection-toolbar";
import { useFaceColors } from "@/components/recording/video/face-colors";
import { PeopleTab } from "@/components/recording/video/people-tab";
import { Button, IconButton } from "@/components/ui/button";
import { Textarea } from "@/components/ui/field";
import { EmptyState } from "@/components/ui/states";
import { needRole } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

/**
 * The page being read, the block of text marked on it, and the zoom; turning pages and choosing blocks. `jump` counts
 * the moves made elsewhere (a page turned, a block chosen, the clock moved), which bring the page into view; `see` is
 * the reader scrolling to a page, which doesn't.
 */
export type DocView = {
  page: number;
  selected: number | null;
  zoom: number;
  jump: number;
  turn: (page: number) => void;
  see: (page: number) => void;
  choose: (block: number) => void;
  setZoom: (z: number) => void;
};

/**
 * The page follows the text: anything that moves to a moment of it (a block chosen, a find match, a summary point, a
 * chat source, ?t=) turns to its page and marks the block. Turning a page moves to its first block, without marking it.
 */
export function useDocView(): DocView {
  const { model, startPage } = useRec();
  const api = usePlayerApi();
  const segs = model.segments;
  const count = Math.max(1, model.pages.length);
  const [page, setPage] = useState(() => clampPage(startPage ?? 0, count));
  const [selected, setSelected] = useState<number | null>(null);
  const [zoom, setZoom] = useState(1);
  const [jump, setJump] = useState(0);
  const quiet = useRef(false);

  useEffect(() => {
    let first = true; // where the clock is as this mounts: not a move
    return api.subscribe((ms) => {
      if (first) {
        first = false;
        return;
      }
      if (quiet.current) {
        quiet.current = false;
        return;
      }
      const i = segmentAt(segs, ms);
      if (i < 0 || !segs[i]) return;
      setPage(segs[i].page ?? 0);
      setSelected(i);
      setJump((n) => n + 1);
    });
  }, [api, segs]);

  const turn = useCallback(
    (p: number) => {
      const to = clampPage(p, count);
      setPage(to);
      setSelected(null);
      setJump((n) => n + 1);
      const t0 = pageStart(segs, to);
      if (t0 != null) {
        quiet.current = true;
        api.seek(t0);
      }
    },
    [api, count, segs],
  );
  const choose = useCallback(
    (i: number) => {
      const s = segs[i];
      if (s) api.seek(s.t0, { manual: true });
    },
    [api, segs],
  );

  // ?page= opens on that page, with the clock at its text (notes and links start there)
  const opened = useRef(false);
  useEffect(() => {
    if (opened.current || startPage == null) return;
    opened.current = true;
    turn(startPage);
  }, [startPage, turn]);

  const see = useCallback((p: number) => setPage(clampPage(p, count)), [count]);

  return { page, selected, zoom, jump, turn, see, choose, setZoom };
}

const MARK: Record<Mark["kind"], string> = {
  selected: "bg-hl-word mix-blend-multiply outline-solid outline-2 outline-blue",
  hit: "outline-solid outline-2 outline-gold",
  "current-hit": "bg-hl-word mix-blend-multiply outline-solid outline-[3px] outline-gold",
};

/**
 * A page drawn, with the blocks to see marked on it, its faces and the kind of object chosen in the Objects tab; a
 * placeholder for a page that couldn't be drawn.
 */
function PageImage({
  page,
  marks,
  faces = [],
  things = [],
  thing = "",
  zoom,
  name,
}: {
  page: PageInfo;
  marks: Mark[];
  faces?: FaceMark[];
  /** Where the chosen kind of object (`thing`) is on the page. */
  things?: Box[];
  thing?: string;
  zoom: number;
  name: string;
}) {
  const blur = useBlur();
  if (!page.image)
    return (
      <div className="mx-auto flex aspect-3/4 w-full max-w-[560px] flex-col items-center justify-center gap-2 rounded-md border border-dashed border-border bg-background p-6 text-center text-[13px] leading-snug text-fg-muted">
        <ImageOff className="size-5" aria-hidden />
        <span>{name} couldn&apos;t be drawn on the server. Its text is in the Text panel.</span>
      </div>
    );
  return (
    <div
      className="relative mx-auto"
      style={{ width: `${zoom * 100}%`, maxWidth: page.width ? `${Math.round(page.width * zoom)}px` : undefined }}
    >
      {/* eslint-disable-next-line @next/next/no-img-element -- a signed link to a page the API drew */}
      <img
        src={page.image}
        width={page.width ?? undefined}
        height={page.height ?? undefined}
        alt={name}
        draggable={false}
        className={cn("block h-auto w-full select-none rounded-[2px] bg-surface shadow-2", blur)}
      />
      <RevealCover />
      {marks.map((m, i) => (
        <span
          key={i}
          aria-hidden
          data-mark={m.kind}
          className={cn("pointer-events-none absolute rounded-[3px]", MARK[m.kind])}
          style={{
            left: `${m.box[0] * 100}%`,
            top: `${m.box[1] * 100}%`,
            width: `${m.box[2] * 100}%`,
            height: `${m.box[3] * 100}%`,
          }}
        />
      ))}
      {faces.length > 0 && <PageFaces faces={faces} />}
      {things.map((b, i) => (
        <span
          key={`o${i}`}
          data-object={thing}
          title={objectName(thing)}
          className="pointer-events-none absolute rounded-xs border-2 border-(--aladdin-green)"
          style={{ left: `${b[0] * 100}%`, top: `${b[1] * 100}%`, width: `${b[2] * 100}%`, height: `${b[3] * 100}%` }}
        >
          <span className="absolute top-[-20px] left-[-2px] whitespace-nowrap rounded-[3px] bg-(--aladdin-green) px-1.5 py-px text-[11px] font-bold text-white">
            {objectName(thing)}
          </span>
        </span>
      ))}
    </div>
  );
}

/** The faces on a page: a box in each one's colour, named as the People tab names it. */
function PageFaces({ faces }: { faces: FaceMark[] }) {
  const { model } = useRec();
  const color = useFaceColors();
  return faces.map((f, i) => {
    const t = model.faces[f.track];
    const label = model.facesMode === "recognize" && t?.name ? t.name : `Face ${f.track + 1}`;
    return (
      <span
        key={i}
        data-face={f.track + 1}
        title={label}
        className="pointer-events-none absolute rounded-xs border-2"
        style={{
          left: `${f.box[0] * 100}%`,
          top: `${f.box[1] * 100}%`,
          width: `${f.box[2] * 100}%`,
          height: `${f.box[3] * 100}%`,
          borderColor: color(f.track),
        }}
      >
        <span
          className="absolute top-[-20px] left-[-2px] whitespace-nowrap rounded-[3px] px-1.5 py-px text-[11px] font-bold text-white"
          style={{ background: color(f.track) }}
        >
          {label}
        </span>
      </span>
    );
  });
}

/** "Page [3] of 12": type a page and press Enter to turn to it. */
function PageField({ view, count }: { view: DocView; count: number }) {
  const { model } = useRec();
  const [draft, setDraft] = useState<string | null>(null);
  const shown = draft ?? String(view.page + 1);
  const go = () => {
    const n = Number(shown);
    if (Number.isFinite(n) && n >= 1) view.turn(n - 1);
    setDraft(null);
  };
  return (
    <label className="tabular flex items-center gap-1.5 text-[13px] text-fg-secondary">
      <span>Page</span>
      <input
        value={shown}
        inputMode="numeric"
        aria-label={`Page, of ${count}`}
        onChange={(e) => setDraft(e.target.value.replace(/\D/g, "").slice(0, 6))}
        onBlur={go}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            go();
          } else if (e.key === "Escape") setDraft(null);
        }}
        className="h-7 w-12 rounded-sm border border-border bg-background px-1.5 text-center text-[13px] text-fg outline-hidden focus:border-blue"
      />
      <span>of {count}</span>
      {model.pages[view.page]?.label && <span className="text-fg-muted">({model.pages[view.page]?.label})</span>}
    </label>
  );
}

/** A page in the pages scrolled one under another: drawn once it's near the view, a blank its size until then. */
function StagePage({
  page,
  view,
  near,
  object,
}: {
  page: PageInfo;
  view: DocView;
  near: boolean;
  object: string | null;
}) {
  const { model, find } = useRec();
  const idx = page.idx;
  const marks = useMemo(
    () => (near ? marksOn(model.segments, idx, view.selected, find.hits, find.index) : []),
    [near, model.segments, idx, view.selected, find.hits, find.index],
  );
  const faces = useMemo(
    () => (near && model.facesMode !== "off" ? facesOn(model.faces, idx) : []),
    [near, model.faces, model.facesMode, idx],
  );
  const things = useMemo(() => {
    const t = near && object ? model.objects.find((o) => o.label === object) : undefined;
    return t ? boxesOnPage(t, idx) : [];
  }, [near, model.objects, object, idx]);
  if (!near)
    return (
      <div
        aria-hidden
        className="mx-auto rounded-[2px] bg-surface shadow-2"
        style={{
          width: `${view.zoom * 100}%`,
          maxWidth: page.width ? `${Math.round(page.width * view.zoom)}px` : undefined,
          aspectRatio: page.width && page.height ? `${page.width} / ${page.height}` : "3 / 4",
        }}
      />
    );
  const name = `Page ${pageNumber(model.pages, idx)}`;
  const shows = descriptionOf(model.descriptions, idx, true); // what it shows, for screen readers
  return (
    <PageImage
      page={page}
      marks={marks}
      faces={faces}
      things={things}
      thing={object ?? ""}
      zoom={view.zoom}
      name={shows ? `${name}: ${shows.text}` : name}
    />
  );
}

/**
 * Every page, one under another, with a toolbar to turn pages and zoom; `object` is the kind of object whose boxes to
 * draw. Scrolling turns the page being read; turning a page, or choosing a block, scrolls to it. Pages are drawn as
 * they near the view, and kept once drawn.
 */
function PageStage({ view, compact, object = null }: { view: DocView; compact?: boolean; object?: string | null }) {
  const { model, state } = useRec();
  const count = model.pages.length;
  const p = model.pages[view.page];
  const box = useRef<HTMLDivElement>(null);
  const els = useRef(new Map<number, HTMLElement>());
  const [drawn, setDrawn] = useState<ReadonlySet<number>>(() => new Set());
  const quiet = useRef(false); // the next scroll is ours: not the reader turning the page
  const frame = useRef(0);

  // pages near the view are drawn (where the browser can tell; the page being read and its neighbours always are)
  useEffect(() => {
    const root = box.current;
    if (!root || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver(
      (entries) => {
        const add = entries
          .filter((e) => e.isIntersecting)
          .map((e) => Number((e.target as HTMLElement).dataset.pageAt));
        if (add.length) setDrawn((was) => (add.every((i) => was.has(i)) ? was : new Set([...was, ...add])));
      },
      { root, rootMargin: "100% 0px" },
    );
    for (const el of els.current.values()) io.observe(el);
    return () => io.disconnect();
  }, [count]);

  // a page turned, a block chosen or the zoom changed: the page, or the block marked on it, comes into view
  const { jump, zoom, page, selected, see } = view;
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      if (!jump) return;
    }
    const root = box.current;
    const el = els.current.get(page);
    if (!root || !el) return;
    const mark = selected != null ? el.querySelector<HTMLElement>("[data-mark=selected]") : null;
    const r = root.getBoundingClientRect();
    const t = (mark ?? el).getBoundingClientRect();
    const top = mark
      ? root.scrollTop + t.top - r.top - Math.max(16, (root.clientHeight - t.height) / 2)
      : root.scrollTop + t.top - r.top - 12;
    quiet.current = true;
    root.scrollTo?.({ top: Math.max(0, top) });
  }, [jump, zoom]); // only on a move, not as the reader scrolls

  const onScroll = () => {
    if (quiet.current) {
      quiet.current = false;
      return;
    }
    if (frame.current) return;
    frame.current = requestAnimationFrame(() => {
      frame.current = 0;
      const root = box.current;
      if (!root) return;
      const tops = model.pages.map((x) => els.current.get(x.idx)?.offsetTop ?? 0);
      const at = model.pages[pageAtOffset(tops, root.scrollTop + root.clientHeight / 3)]?.idx;
      if (at != null && at !== page) see(at);
    });
  };
  useEffect(() => () => cancelAnimationFrame(frame.current), []);

  return (
    <section aria-label="Pages" className="flex min-h-0 min-w-0 flex-col bg-surface-neutral">
      <div className="flex flex-wrap items-center gap-1.5 border-b border-border bg-background px-3 py-2">
        <IconButton label="Previous page" size={32} disabled={view.page <= 0} onClick={() => view.turn(view.page - 1)}>
          <ChevronLeft />
        </IconButton>
        {count > 0 && <PageField view={view} count={count} />}
        <IconButton
          label="Next page"
          size={32}
          disabled={view.page >= count - 1}
          onClick={() => view.turn(view.page + 1)}
        >
          <ChevronRight />
        </IconButton>
        <span className="min-w-0 flex-1 truncate px-1 text-[12px] text-fg-muted">
          {p && !compact ? textNote(p) : ""}
        </span>
        <IconButton
          label="Zoom out"
          size={32}
          disabled={view.zoom <= 0.5}
          onClick={() => view.setZoom(zoomStep(view.zoom, -1))}
        >
          <Minus />
        </IconButton>
        <button
          type="button"
          onClick={() => view.setZoom(1)}
          aria-label={view.zoom === 1 ? "Fits the width" : `Zoom ${Math.round(view.zoom * 100)}%: fit the width`}
          className="tabular h-7 min-w-[52px] rounded-pill px-2 text-[12px] font-semibold text-fg-secondary hover:bg-surface-neutral"
        >
          {view.zoom === 1 ? "Fit" : `${Math.round(view.zoom * 100)}%`}
        </button>
        <IconButton
          label="Zoom in"
          size={32}
          disabled={view.zoom >= 3}
          onClick={() => view.setZoom(zoomStep(view.zoom, 1))}
        >
          <Plus />
        </IconButton>
      </div>
      <div
        ref={box}
        onScroll={onScroll}
        className={cn(
          "relative flex min-h-0 flex-1 flex-col gap-4 overflow-auto p-4",
          compact && "max-h-[58dvh] min-h-[320px] gap-3 p-3",
        )}
      >
        {count > 0 ? (
          model.pages.map((x) => (
            <div
              key={x.idx}
              data-page-at={x.idx}
              aria-current={x.idx === page ? "page" : undefined}
              ref={(el) => {
                if (el) els.current.set(x.idx, el);
                else els.current.delete(x.idx);
              }}
              className="shrink-0"
            >
              <StagePage page={x} view={view} near={Math.abs(x.idx - page) <= 1 || drawn.has(x.idx)} object={object} />
            </div>
          ))
        ) : state.phase === "processing" || state.phase === "analyzing" ? (
          <EmptyState icon={<Loader2 className="animate-spin" />} title="Drawing its pages" className="py-16">
            Its pages appear here as soon as they&apos;re drawn and read.
          </EmptyState>
        ) : (
          <EmptyState icon={<FileText />} title="No pages yet" className="py-16">
            Its pages are drawn when its pipeline runs. Run it again from Reprocess.
          </EmptyState>
        )}
      </div>
    </section>
  );
}

/** Thumbnails of every page, the current one marked; choosing one turns to it. */
function Thumbs({ view }: { view: DocView }) {
  const { model } = useRec();
  const blur = useBlur();
  const list = useRef<HTMLOListElement>(null);
  useEffect(() => {
    list.current?.querySelector(`[data-page="${view.page}"]`)?.scrollIntoView({ block: "nearest" });
  }, [view.page]);
  return (
    <nav aria-label="Pages to choose" className="min-h-0 overflow-y-auto border-r border-border bg-surface px-2.5 py-3">
      <ol ref={list} className="flex flex-col gap-2.5">
        {model.pages.map((p) => {
          const on = p.idx === view.page;
          const n = pageNumber(model.pages, p.idx);
          return (
            <li key={p.idx}>
              <button
                type="button"
                data-page={p.idx}
                aria-current={on ? "page" : undefined}
                aria-label={`Page ${n}`}
                onClick={() => view.turn(p.idx)}
                className={cn(
                  "flex w-full flex-col items-center gap-1 rounded-sm p-1.5 transition-colors duration-fast",
                  on ? "bg-hl outline-solid outline-2 outline-blue" : "hover:bg-surface-neutral",
                )}
              >
                {p.thumb ? (
                  // eslint-disable-next-line @next/next/no-img-element -- a signed link to a thumbnail the API drew
                  <img
                    src={p.thumb}
                    alt=""
                    loading="lazy"
                    className={cn("w-full rounded-[2px] bg-background shadow-1", blur)}
                  />
                ) : (
                  <span className="grid aspect-3/4 w-full place-items-center rounded-[2px] bg-surface-neutral text-fg-muted">
                    <FileText className="size-4" aria-hidden />
                  </span>
                )}
                <span className="tabular text-[11.5px] leading-none text-fg-muted">{n}</span>
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

/** Correct a block's text (editors): saved as a correction, kept in its history like a transcript's. */
function BlockEditor({ idx, text, onDone }: { idx: number; text: string; onDone: () => void }) {
  const { id } = useRec();
  const { editSegment } = useRecordingActions(id);
  const [value, setValue] = useState(text);
  const save = async () => {
    const v = value.trim();
    if (v && v !== text.trim()) await editSegment.mutateAsync({ idx, text: v }).catch(() => undefined);
    onDone();
  };
  return (
    <div className="flex flex-col gap-2 rounded-sm border border-blue-border bg-blue-surface p-2">
      <Textarea
        aria-label="The block's text"
        value={value}
        rows={Math.min(10, Math.max(2, Math.ceil(value.length / 60)))}
        autoFocus
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape") onDone();
          else if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void save();
        }}
      />
      <div className="flex justify-end gap-2">
        <Button size="sm" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
        <Button size="sm" variant="primary" disabled={editSegment.isPending} onClick={() => void save()}>
          {editSegment.isPending ? "Saving…" : "Save"}
        </Button>
      </div>
    </div>
  );
}

/** The text, page by page: choosing a block turns to its page and marks it there; find marks matches in both. With
 * `follow` (beside the page, in a panel of its own) the page's text, or the block chosen, scrolls into view. */
export function PageText({ view, follow = true }: { view: DocView; follow?: boolean }) {
  const { id, model, find, canEdit, ns, editing, setEditing, focusHighlight } = useRec();
  const by = useMemo(() => blocksByPage(model.segments), [model.segments]);
  const highlights = useHighlights(id).data;
  const root = useRef<HTMLDivElement>(null);
  const hits = useMemo(() => {
    const out = new Map<number, { start: number; end: number }[]>();
    for (const h of find.hits) out.set(h.seg, [...(out.get(h.seg) ?? []), h]);
    return out;
  }, [find.hits]);
  const current = find.hits[find.index];
  const [open, setOpen] = useState<number | null>(null);
  const sections = useRef(new Map<number, HTMLElement>());
  const blocks = useRef(new Map<number, HTMLElement>());

  // the page turned, or a block chosen (here, on the page or by a link): it comes into view (not as the page opens)
  const opened = useRef(false);
  useEffect(() => {
    if (!opened.current) {
      opened.current = true;
      if (view.selected == null && view.page === 0) return;
    }
    if (!follow) return;
    const el = view.selected != null ? blocks.current.get(view.selected) : sections.current.get(view.page);
    el?.scrollIntoView?.({ block: "nearest" });
  }, [view.page, view.selected, follow]);
  // each find match turns to its page
  const { choose } = view;
  useEffect(() => {
    if (current) choose(current.seg);
  }, [current, choose]);

  const pages = model.pages.length ? model.pages : [];
  return (
    <div ref={root} data-own-selection className="flex flex-col gap-4">
      {!editing && <SelectionToolbar box={root} />}
      <div className="flex flex-wrap items-center gap-2">
        <FindBar what="the text" />
        <Button
          size="sm"
          variant={editing ? "primary" : "secondary"}
          icon={<Pencil />}
          aria-pressed={editing}
          disabled={!canEdit}
          disabledReason={needRole("editor", ns)}
          onClick={() => {
            setEditing(!editing);
            setOpen(null);
          }}
        >
          {editing ? "Done" : "Correct text"}
        </Button>
      </div>
      {editing && (
        <p className="text-[12.5px] leading-snug text-fg-muted">
          Choose a block to correct what was read. Corrections are kept in History.
        </p>
      )}
      {!pages.length && <p className="text-[13.5px] text-fg-muted">Its text appears here once its pages are read.</p>}
      {pages.map((p) => {
        const list = by.get(p.idx) ?? [];
        const on = p.idx === view.page;
        const shows = descriptionOf(model.descriptions, p.idx, true);
        return (
          <section
            key={p.idx}
            ref={(el) => {
              if (el) sections.current.set(p.idx, el);
              else sections.current.delete(p.idx);
            }}
            aria-labelledby={`page-${p.idx}`}
            className={cn("flex flex-col gap-1.5 border-l-2 pl-3", on ? "border-blue" : "border-transparent")}
          >
            <h3 id={`page-${p.idx}`} className="flex items-baseline gap-2">
              <button
                type="button"
                onClick={() => view.turn(p.idx)}
                className="text-[12px] font-bold uppercase tracking-[.04em] text-fg-secondary hover:text-fg hover:underline"
              >
                Page {pageNumber(model.pages, p.idx)}
              </button>
              <span className="flex items-center gap-1 text-[11.5px] text-fg-muted">
                {p.text === "ocr" && <ScanSearch className="size-3" aria-hidden />}
                {textNote(p)}
              </span>
            </h3>
            {shows && (
              <p className="rounded-md bg-surface-neutral px-2.5 py-2 text-[13px] leading-normal text-fg-secondary">
                <span className="mr-1.5 inline-flex items-center gap-1 text-[11.5px] font-semibold text-fg-muted">
                  <Eye className="size-3" aria-hidden />
                  What it shows
                </span>
                {shows.text}
              </p>
            )}
            {list.length === 0 && <p className="text-[13px] text-fg-muted">No text on this page.</p>}
            {list.map((i) => {
              const s = model.segments[i];
              if (open === i) return <BlockEditor key={i} idx={i} text={s.text} onDone={() => setOpen(null)} />;
              const runs = highlightRuns(
                s.text,
                (hits.get(i) ?? []).map((h) => ({
                  ...h,
                  kind: current && current.seg === i && current.start === h.start ? "hit-current" : "hit",
                })),
                highlights?.length ? highlightRanges(s, highlights) : [],
              );
              const act = () => {
                const sel = typeof window !== "undefined" ? window.getSelection() : null;
                if (!editing && sel && !sel.isCollapsed) return; // picking words, not choosing the block
                if (editing) setOpen(i);
                else view.choose(i);
              };
              return (
                <p
                  key={i}
                  ref={(el) => {
                    if (el) blocks.current.set(i, el);
                    else blocks.current.delete(i);
                  }}
                  role="button"
                  tabIndex={0}
                  data-block={i}
                  data-seg={i}
                  aria-current={view.selected === i ? "true" : undefined}
                  aria-label={editing ? `Correct: ${s.text.slice(0, 80)}` : undefined}
                  onClick={act}
                  onKeyDown={(e: ReactKeyboardEvent) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      act();
                    }
                  }}
                  className={cn(
                    "cursor-pointer rounded-xs px-1.5 py-1 font-serif text-[15.5px] leading-[1.6] text-fg outline-hidden focus-visible:shadow-[0_0_0_2px_var(--aladdin-blue)]",
                    view.selected === i ? "bg-hl" : editing ? "hover:bg-blue-surface" : "hover:bg-surface-neutral",
                  )}
                >
                  {runs.map((r, k) => {
                    const run = r.kind ? (
                      <mark
                        data-hit={r.kind === "hit-current" ? "current" : undefined}
                        className={cn(
                          "rounded-[3px] text-fg",
                          r.kind === "hit-current"
                            ? "bg-gold-surface shadow-[0_0_0_2px_var(--aladdin-gold)]"
                            : "bg-hl-word shadow-[0_0_0_2px_var(--hl-word)]",
                        )}
                      >
                        {r.text}
                      </mark>
                    ) : (
                      <span>{r.text}</span>
                    );
                    return r.hl ? (
                      <HighlightMark key={k} hl={r.hl} onOpen={focusHighlight}>
                        {run}
                      </HighlightMark>
                    ) : (
                      <span key={k}>{run}</span>
                    );
                  })}
                </p>
              );
            })}
          </section>
        );
      })}
    </div>
  );
}

/**
 * A document's or an image's page (R1a): its pages to look at (thumbnails, the page, zoom) beside its text, page by
 * page, and the resource's panels. On a phone the page comes first and the panels under it. ←/→ (or Page Up/Down)
 * turn pages, + and − zoom, / finds in the text.
 */
export function DocumentLayout({ compact }: { compact: boolean }) {
  const r = useRec();
  const { model, rec, tab, setTab, find } = r;
  const view = useDocView();
  const wide = useMediaQuery("(min-width: 1280px)");
  const notes = useNotes(r.id).data?.length ?? 0;
  const open = openThreads(useComments(r.id).data ?? []);
  const visual = useVisualNotes(r.jobs);
  const [object, setObject] = useState<string | null>(null);
  const tabs: TabDef[] = [
    { value: "pages", label: "Text", count: model.segments.length || undefined },
    { value: "summary", label: "Summary" },
    { value: "entities", label: "Entities" },
    { value: "similar", label: "Similar" },
    { value: "chat", label: "Chat" },
    { value: "notes", label: "Notes", count: notes || undefined },
    { value: "comments", label: "Comments", count: open || undefined },
    // people on its pages, where the namespace looks for faces
    ...(model.facesMode === "off"
      ? []
      : [{ value: "people" as const, label: "People", count: model.faces.length || undefined }]),
    { value: "objects", label: "Objects", count: model.objects.length || undefined },
    { value: "history", label: "History" },
  ];
  const current: PanelTab = [...tabs, ...MORE_TABS].some((t) => t.value === tab) ? tab : "pages";
  // on a phone the text is under the page: turning pages doesn't scroll the screen away from it
  const body =
    current === "pages" ? (
      <PageText view={view} follow={!compact} />
    ) : current === "people" ? (
      <PeopleTab onPage={view.turn} />
    ) : current === "objects" ? (
      <ObjectsTab selected={object} onSelect={setObject} onPage={view.turn} why={visual.objectsWhy} />
    ) : (
      <PanelBody tab={current} />
    );

  // ←/→ and Page Up/Down turn pages, + and − zoom, / finds in the text (not while typing, or in a menu or dialog)
  const { turn, setZoom, page, zoom } = view;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target instanceof Element ? e.target : null;
      if (t?.closest("input, textarea, select, [contenteditable=true], [role=tablist], [role=menu], [role=dialog]"))
        return;
      if (document.querySelector("[role=dialog][data-state=open]")) return;
      if (e.key === "ArrowLeft" || e.key === "PageUp") turn(page - 1);
      else if (e.key === "ArrowRight" || e.key === "PageDown") turn(page + 1);
      else if (e.key === "+" || e.key === "=") setZoom(zoomStep(zoom, 1));
      else if (e.key === "-") setZoom(zoomStep(zoom, -1));
      else if (e.key === "/") {
        setTab("pages");
        find.setOpen(true);
        setTimeout(() => document.querySelector<HTMLInputElement>('input[aria-label="Find in the text"]')?.focus(), 0);
      } else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [turn, setZoom, page, zoom, setTab, find]);

  if (compact)
    return (
      // the tabs measure themselves in a row as wide as all of them: it mustn't widen the page
      <div className="flex flex-col overflow-x-hidden">
        <div className="flex items-center gap-1.5 border-b border-border px-2 pb-2 pt-1">
          <Link
            href="/library"
            aria-label="Back to the Library"
            className="grid size-11 shrink-0 place-items-center rounded-full hover:bg-surface-neutral"
          >
            <ChevronLeft className="size-[22px]" />
          </Link>
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-[15px] font-bold leading-tight text-fg">{model.title}</h1>
            <p className="tabular truncate text-[12px] text-fg-muted">
              {[
                homeText(r.ns, rec.collection_path, true),
                pagesSummary(model.pages, model.media.kind === "image" ? "image" : "document"),
              ]
                .filter(Boolean)
                .join(" · ")}
            </p>
          </div>
          <HeaderActions compact />
        </div>
        <Banners className="mx-3 mt-2" />
        <PageStage view={view} compact object={object} />
        <div className="flex min-h-[320px] flex-col border-t border-border">
          <PanelTabs tabs={tabs} more={MORE_TABS} value={current} onChange={setTab} idBase="doc" className="px-3.5" />
          <PanelScroll id="doc" tab={current} className="overflow-visible">
            {body}
          </PanelScroll>
        </div>
      </div>
    );

  return (
    <div className="flex h-[calc(100dvh-4rem)] min-h-[600px] flex-col overflow-hidden">
      <RecordingHeader />
      <div
        className="grid min-h-0 flex-1"
        style={{
          gridTemplateColumns: wide
            ? "132px minmax(0,1fr) clamp(380px,36%,500px)"
            : "minmax(0,1fr) clamp(340px,42%,460px)",
        }}
      >
        {wide && <Thumbs view={view} />}
        <PageStage view={view} object={object} />
        <aside aria-label="Panels" className="flex min-h-0 min-w-0 flex-col border-l border-border bg-background">
          <PanelTabs tabs={tabs} more={MORE_TABS} value={current} onChange={setTab} idBase="doc" />
          <PanelScroll id="doc" tab={current}>
            {body}
          </PanelScroll>
        </aside>
      </div>
    </div>
  );
}
