"use client";

import { ChevronDown, ChevronLeft, ChevronRight, ChevronUp, Download, ImageOff } from "lucide-react";
import { useMemo, useRef, useState, type ReactNode } from "react";

import type { PublicRecording } from "@/app/openapi-client/types.gen";
import { findLines, firstPage, lineAt, linesByPage, markParts, pageName } from "@/components/public/model";
import { Card, ClosedNote } from "@/components/public/parts";
import { Button, IconButton } from "@/components/ui/button";
import { SearchInput } from "@/components/ui/field";
import { plural } from "@/lib/format";
import { cn } from "@/lib/utils";

type Rec = PublicRecording;
type Text = NonNullable<Rec["transcript"]>;

function ownWord(file: string | null | undefined): string {
  const w = file || "original";
  return w.charAt(0).toUpperCase() + w.slice(1);
}

/** How many pages a document has: its pages when the visitor may see them, else as many as its text is on. */
function pageCount(rec: Rec): number {
  const drawn = rec.media?.pages?.length ?? 0;
  const read = (rec.transcript?.segments ?? []).reduce((n, s) => Math.max(n, (s.p ?? 0) + 1), 0);
  return Math.max(drawn, read, 1);
}

/**
 * A document's or an image's public page (docs/access.md): its pages, when they're open to the visitor, with its file
 * to save; its text page by page, which turns the pages to where a line is; its sections by page. `head` and `aside`
 * are what every resource's page has (the title, notes, description, downloads and files).
 */
export function DocumentBody({
  rec,
  signedIn,
  start,
  page: asked,
  head,
  aside,
}: {
  rec: Rec;
  signedIn: boolean;
  start: number | null;
  page: number | null;
  head: ReactNode;
  aside: ReactNode;
}) {
  const segments = rec.transcript?.segments ?? [];
  const [page, setPage] = useState(() =>
    firstPage(pageCount(rec), segments, asked, start != null ? start * 1000 : null),
  );
  return (
    <article className="mx-auto flex w-full max-w-[1120px] flex-col gap-5 px-4 py-6 sm:px-6">
      {head}
      <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="flex min-w-0 flex-col gap-5">
          <PagesCard rec={rec} signedIn={signedIn} page={page} onPage={setPage} />
          <TextCard rec={rec} signedIn={signedIn} page={page} onPage={setPage} />
        </div>
        <aside className="flex min-w-0 flex-col gap-5">
          <SectionsCard rec={rec} signedIn={signedIn} onPage={setPage} />
          {aside}
        </aside>
      </div>
    </article>
  );
}

function PagesCard({
  rec,
  signedIn,
  page,
  onPage,
}: {
  rec: Rec;
  signedIn: boolean;
  page: number;
  onPage: (page: number) => void;
}) {
  const m = rec.media;
  const image = rec.media_kind === "image";
  const title = image ? "Image" : "Pages";
  if (!m)
    return rec.closed.includes("media") ? (
      <Card title={title}>
        <ClosedNote part="media" rec={rec} signedIn={signedIn} />
      </Card>
    ) : null;
  const pages = m.pages ?? [];
  const p = pages[page];
  const name = `Page ${pageName(pages, page)}`;
  // its own file: the image, the PDF, or (made into a PDF) the Word document, email, … it was
  const own = image ? "Image" : m.pdf ? ownWord(m.file) : "PDF";
  return (
    <Card
      title={title}
      extra={
        <div className="flex flex-wrap justify-end gap-1">
          {m.pdf && (
            <Button asChild size="sm" variant="ghost">
              <a href={m.pdf} download aria-label="Download the PDF">
                <Download aria-hidden />
                PDF
              </a>
            </Button>
          )}
          <Button asChild size="sm" variant="ghost">
            <a href={m.url} download aria-label={`Download the ${own === "PDF" ? own : own.toLowerCase()}`}>
              <Download aria-hidden />
              {m.pdf ? own : `Download the ${own === "PDF" ? own : own.toLowerCase()}`}
            </a>
          </Button>
        </div>
      }
    >
      {pages.length > 1 && (
        <div className="flex items-center gap-2">
          <IconButton label="Previous page" size={32} disabled={page <= 0} onClick={() => onPage(page - 1)}>
            <ChevronLeft />
          </IconButton>
          <span aria-live="polite" className="tabular text-[13px] font-medium text-fg-secondary">
            {name} of {pages.length}
          </span>
          <IconButton label="Next page" size={32} disabled={page >= pages.length - 1} onClick={() => onPage(page + 1)}>
            <ChevronRight />
          </IconButton>
        </div>
      )}
      {p?.image ? (
        // eslint-disable-next-line @next/next/no-img-element -- a signed link to a page the API drew
        <img
          src={p.image}
          width={p.width ?? undefined}
          height={p.height ?? undefined}
          alt={image && pages.length === 1 ? rec.title || "The image" : name}
          className="mx-auto block h-auto w-full max-w-[720px] rounded-[2px] border border-border bg-surface"
        />
      ) : (
        <div className="flex aspect-[3/4] w-full flex-col items-center justify-center gap-2 rounded-md border border-dashed border-border bg-surface p-6 text-center text-[13px] text-fg-muted">
          <ImageOff aria-hidden className="size-5" />
          {name} couldn’t be drawn.
        </div>
      )}
      {pages.length > 1 && (
        <nav aria-label="Pages to choose" className="flex min-w-0 gap-2 overflow-x-auto pb-1">
          {pages.map((x) => (
            <button
              key={x.idx}
              type="button"
              onClick={() => onPage(x.idx)}
              aria-label={`Page ${pageName(pages, x.idx)}`}
              aria-current={x.idx === page ? "page" : undefined}
              className={cn(
                "w-14 shrink-0 overflow-hidden rounded-[3px] border-2 bg-surface",
                x.idx === page ? "border-blue" : "border-transparent hover:border-blue-border",
              )}
            >
              {x.thumb ? (
                // eslint-disable-next-line @next/next/no-img-element -- a signed link to a page the API drew
                <img src={x.thumb} alt="" loading="lazy" className="block h-auto w-full" />
              ) : (
                <span className="grid aspect-[3/4] place-items-center text-[11px] text-fg-muted">
                  {pageName(pages, x.idx)}
                </span>
              )}
            </button>
          ))}
        </nav>
      )}
    </Card>
  );
}

function TextCard({
  rec,
  signedIn,
  page,
  onPage,
}: {
  rec: Rec;
  signedIn: boolean;
  page: number;
  onPage: (page: number) => void;
}) {
  const t = rec.transcript;
  if (!t)
    return rec.closed.includes("transcript") ? (
      <Card title="Text">
        <ClosedNote part="transcript" rec={rec} signedIn={signedIn} />
      </Card>
    ) : null;
  return <DocumentText rec={rec} t={t} page={page} onPage={onPage} />;
}

/** The text page by page, with find; a page's name turns the pages to it, as finding a line on another page does. */
function DocumentText({ rec, t, page, onPage }: { rec: Rec; t: Text; page: number; onPage: (page: number) => void }) {
  const [query, setQuery] = useState("");
  const [at, setAt] = useState(0);
  const lines = useRef<(HTMLParagraphElement | null)[]>([]);
  const hits = useMemo(() => findLines(t.segments, query), [t.segments, query]);
  const groups = useMemo(() => linesByPage(t.segments), [t.segments]);
  const pages = rec.media?.pages ?? null;
  const turns = Boolean(pages?.length && pages.length > 1);
  const go = (n: number) => {
    if (!hits.length) return;
    const k = (n + hits.length) % hits.length;
    setAt(k);
    if (turns) onPage(t.segments[hits[k]].p ?? 0);
    lines.current[hits[k]]?.scrollIntoView({ block: "center", behavior: "smooth" });
  };
  return (
    <Card title="Text" extra={<span className="text-[12px] text-fg-muted">{plural(groups.length, "page")}</span>}>
      <div className="flex items-center gap-2">
        <div className="min-w-0 flex-1">
          <SearchInput
            aria-label="Find in the text"
            placeholder="Find in the text"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setAt(0);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                go(e.shiftKey ? at - 1 : at + 1);
              }
            }}
          />
        </div>
        {query.trim() && (
          <>
            <span aria-live="polite" className="tabular whitespace-nowrap text-[12.5px] text-fg-secondary">
              {hits.length ? `${at + 1} of ${hits.length}` : "Nothing matches"}
            </span>
            <IconButton label="Previous match" size={32} onClick={() => go(at - 1)} disabled={!hits.length}>
              <ChevronUp />
            </IconButton>
            <IconButton label="Next match" size={32} onClick={() => go(at + 1)} disabled={!hits.length}>
              <ChevronDown />
            </IconButton>
          </>
        )}
      </div>
      <div className="flex flex-col gap-4">
        {groups.map((g) => {
          const name = `Page ${pageName(pages, g.page)}`;
          return (
            <section
              key={g.page}
              aria-label={groups.length > 1 ? name : "The text"}
              className={cn("flex flex-col gap-1 rounded-sm px-2 py-1.5", turns && g.page === page && "bg-surface")}
            >
              {groups.length > 1 &&
                (turns ? (
                  <button
                    type="button"
                    onClick={() => onPage(g.page)}
                    aria-label={`Show ${name.toLowerCase()}`}
                    aria-current={g.page === page ? "page" : undefined}
                    className="w-fit text-[12px] font-bold uppercase tracking-[0.04em] text-fg-accent hover:underline"
                  >
                    {name}
                  </button>
                ) : (
                  <span className="text-[12px] font-bold uppercase tracking-[0.04em] text-fg-muted">{name}</span>
                ))}
              {g.lines.map((i) => (
                <p
                  key={i}
                  ref={(el) => {
                    lines.current[i] = el;
                  }}
                  className={cn(
                    "whitespace-pre-line rounded-[3px] font-serif text-[15.5px] leading-[1.6] text-fg",
                    query.trim() && hits[at] === i && "outline outline-2 outline-blue-border",
                  )}
                >
                  {markParts(t.segments[i].text, query).map((p, k) =>
                    p.hit ? (
                      <mark key={k} className="rounded-[2px] bg-hl-word text-fg">
                        {p.text}
                      </mark>
                    ) : (
                      <span key={k}>{p.text}</span>
                    ),
                  )}
                </p>
              ))}
            </section>
          );
        })}
      </div>
    </Card>
  );
}

/** A document's sections (its index), each on the page it starts on. */
function SectionsCard({ rec, signedIn, onPage }: { rec: Rec; signedIn: boolean; onPage: (page: number) => void }) {
  const segments = rec.transcript?.segments ?? [];
  const pages = rec.media?.pages ?? null;
  if (!rec.chapters?.length)
    return rec.closed.includes("index") ? (
      <Card title="Sections">
        <ClosedNote part="index" rec={rec} signedIn={signedIn} />
      </Card>
    ) : null;
  return (
    <Card title="Sections">
      <ol className="flex flex-col gap-1">
        {rec.chapters.map((c, i) => {
          const at = lineAt(segments, c.t0);
          const p = at >= 0 ? (segments[at].p ?? 0) : null;
          return (
            <li key={i} className="grid grid-cols-[44px_minmax(0,1fr)] gap-2 text-[13.5px] leading-[1.45]">
              {p != null && pages?.length ? (
                <button
                  type="button"
                  onClick={() => onPage(p)}
                  className="tabular h-fit text-left text-[12.5px] font-medium text-fg-accent hover:underline"
                  aria-label={`Show page ${pageName(pages, p)}`}
                >
                  p. {pageName(pages, p)}
                </button>
              ) : (
                <span className="tabular text-[12.5px] text-fg-muted">{p != null ? `p. ${p + 1}` : ""}</span>
              )}
              <span className="text-fg">{c.title || "Untitled"}</span>
            </li>
          );
        })}
      </ol>
    </Card>
  );
}
