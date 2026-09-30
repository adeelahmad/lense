"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, ChevronDown, ChevronUp, Clock3, Download, FileQuestion, Lock } from "lucide-react";
import Link from "next/link";
import { useSession } from "next-auth/react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { Public } from "@/app/openapi-client";
import type { PublicRecording } from "@/app/openapi-client/types.gen";
import { AccessBadge } from "@/components/access/access-fields";
import type { AccessPart } from "@/components/access/model";
import { first, type Meta } from "@/components/iiif/metadata-model";
import { PlayerProvider, usePlayerApi, usePlayerState } from "@/components/player/media";
import { PlayButton, SkipButton, SpeedMenu, TimeReadout, VolumeControl } from "@/components/player/transport";
import { useMediaQuery } from "@/components/player/use-media-query";
import { Waveform, type WaveLane } from "@/components/player/waveform";
import type { Segment } from "@/components/recording/model";
import { closedNote, descriptionRows, findLines, lineAt, markParts, safeHref } from "@/components/public/model";
import { useSignInHref } from "@/components/public/public-shell";
import { Banner } from "@/components/ui/banner";
import { Button, IconButton } from "@/components/ui/button";
import { SearchInput } from "@/components/ui/field";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { shortDate, tc } from "@/lib/format";
import { cn } from "@/lib/utils";

type Rec = PublicRecording;

const KIND: Record<Rec["media_kind"], string> = { audio: "Audio", video: "Video", transcript: "Transcript only" };

/**
 * A recording's public page (docs/access.md): all of it for people with permission; for everyone else a public
 * recording's page, description and the parts open to everyone, with a lock where the rest would be. A restricted
 * recording shows signed-in people its title behind a lock ("content locked").
 */
export function PublicRecordingView({ id }: { id: number }) {
  const client = useApiClient();
  const { status } = useSession();
  const signedIn = status === "authenticated";
  const q = useQuery({
    queryKey: ["public", "recording", id, signedIn],
    queryFn: () => data(Public.getPublicRecording({ client, path: { rid: id } })),
    enabled: status !== "loading",
  });
  const title = q.data?.title;
  useEffect(() => {
    if (title) document.title = `${title} · Lens Archive`;
  }, [title]);

  if (q.isPending) return <PageSkeleton />;
  if (q.isError)
    return q.error instanceof ApiError && q.error.status === 404 ? (
      <NotAvailable signedIn={signedIn} />
    ) : (
      <Centered>
        <EmptyState
          tone="error"
          icon={<FileQuestion />}
          title="Couldn’t load this recording"
          actions={<Button onClick={() => q.refetch()}>Try again</Button>}
        >
          {q.error.message}
        </EmptyState>
      </Centered>
    );
  if (q.data.view === "locked") return <Locked rec={q.data} />;
  return <RecordingBody rec={q.data} signedIn={signedIn} />;
}

function Centered({ children }: { children: ReactNode }) {
  return <div className="mx-auto flex w-full max-w-[560px] flex-1 flex-col justify-center px-4 py-12">{children}</div>;
}

function NotAvailable({ signedIn }: { signedIn: boolean }) {
  const signIn = useSignInHref();
  return (
    <Centered>
      <EmptyState
        icon={<FileQuestion />}
        title="This recording isn’t available"
        actions={
          signedIn ? undefined : (
            <Button asChild variant="primary">
              <Link href={signIn}>Sign in</Link>
            </Button>
          )
        }
      >
        {signedIn
          ? "It may not be public, or the link may be wrong."
          : "It may not be public, or you may need to sign in to see it."}
      </EmptyState>
    </Centered>
  );
}

/** A restricted recording, for someone signed in without permission: its title behind a lock. */
function Locked({ rec }: { rec: Rec }) {
  return (
    <Centered>
      <section className="flex flex-col items-center gap-3 rounded-lg border border-border bg-background px-6 py-8 text-center">
        <span className="grid size-12 place-items-center rounded-full bg-surface-neutral text-fg-secondary">
          <Lock aria-hidden className="size-5" />
        </span>
        <span className="text-[12.5px] font-semibold uppercase tracking-[0.04em] text-fg-muted">Content locked</span>
        <h1 className="text-[20px] font-bold leading-tight text-fg">{rec.title || "Untitled"}</h1>
        <p className="max-w-[44ch] text-[14px] leading-[1.5] text-fg-secondary">
          This recording is restricted: only members of {rec.namespace ?? "its namespace"}, and people it’s shared with,
          can open it.
        </p>
      </section>
    </Centered>
  );
}

function PageSkeleton() {
  return (
    <div
      className="mx-auto flex w-full max-w-[1120px] flex-col gap-5 px-4 py-6 sm:px-6"
      aria-busy="true"
      aria-label="Loading the recording"
    >
      <Skeleton className="h-4 w-24" />
      <Skeleton className="h-8 w-3/5" />
      <Skeleton className="h-4 w-2/5" />
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    </div>
  );
}

function RecordingBody({ rec, signedIn }: { rec: Rec; signedIn: boolean }) {
  const meta = (rec.description ?? {}) as Meta;
  const segments = useMemo(() => rec.transcript?.segments ?? [], [rec.transcript]);
  const duration = rec.duration_ms || (segments.length ? segments[segments.length - 1].t1 : 0);
  return (
    <PlayerProvider hasMedia={Boolean(rec.media)} durationMs={duration} speech={segments}>
      <article className="mx-auto flex w-full max-w-[1120px] flex-col gap-5 px-4 py-6 sm:px-6">
        <PageHead rec={rec} meta={meta} duration={duration} />
        {rec.member && <MemberNote rec={rec} />}
        <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
          <div className="flex min-w-0 flex-col gap-5">
            <MediaCard rec={rec} duration={duration} signedIn={signedIn} />
            <TranscriptCard rec={rec} signedIn={signedIn} />
          </div>
          <aside className="flex min-w-0 flex-col gap-5">
            <ChaptersCard rec={rec} signedIn={signedIn} />
            <DescriptionCard meta={meta} />
            <DownloadsCard rec={rec} />
          </aside>
        </div>
      </article>
    </PlayerProvider>
  );
}

function PageHead({ rec, meta, duration }: { rec: Rec; meta: Meta; duration: number }) {
  const summary = first(meta.summary);
  return (
    <header className="flex flex-col gap-2">
      {rec.namespace && <span className="text-[12.5px] font-semibold text-fg-muted">{rec.namespace}</span>}
      <h1 className="text-[24px] font-bold leading-[1.2] text-fg sm:text-[28px]">{rec.title || "Untitled"}</h1>
      <div className="tabular flex flex-wrap items-center gap-x-3.5 gap-y-1.5 text-[13px] text-fg-secondary">
        {rec.recorded_at && <time dateTime={rec.recorded_at}>{shortDate(rec.recorded_at)}</time>}
        {duration > 0 && (
          <span className="flex items-center gap-[5px]">
            <Clock3 aria-hidden className="size-3.5" />
            {tc(duration)}
          </span>
        )}
        <span>{KIND[rec.media_kind]}</span>
        <AccessBadge value={rec} />
      </div>
      {summary && <p className="max-w-[72ch] text-[15px] leading-[1.55] text-fg">{summary}</p>}
    </header>
  );
}

function MemberNote({ rec }: { rec: Rec }) {
  return (
    <Banner
      action={
        <Button asChild size="sm">
          <Link href={`/recordings/${rec.id}`}>Open in the workspace</Link>
        </Button>
      }
    >
      You’re a member of <b>{rec.namespace}</b>, so you see all of it.
      {rec.access === "public"
        ? " Visitors see the parts open to everyone."
        : ` It’s ${rec.access}: visitors don’t see this page.`}
    </Banner>
  );
}

function Card({
  title,
  extra,
  children,
  className,
}: {
  title: string;
  extra?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={cn("flex flex-col gap-3 rounded-md border border-border bg-background p-4", className)}
      aria-label={title}
    >
      <div className="flex items-center gap-2">
        <h2 className="text-[14px] font-bold text-fg">{title}</h2>
        <span className="flex-1" />
        {extra}
      </div>
      {children}
    </section>
  );
}

function ClosedNote({ part, rec, signedIn }: { part: AccessPart; rec: Rec; signedIn: boolean }) {
  const signIn = useSignInHref();
  const note = closedNote(part, { ns: rec.namespace, signedIn, kind: rec.media_kind });
  return (
    <div className="flex items-start gap-3 rounded-md bg-surface px-3.5 py-3">
      <Lock aria-hidden className="mt-0.5 size-4 shrink-0 text-fg-muted" />
      <div className="flex min-w-0 flex-col gap-1">
        <b className="text-[13.5px] font-semibold text-fg">{note.title}</b>
        <span className="text-[13px] leading-[1.45] text-fg-secondary">{note.body}</span>
        {!signedIn && (
          <Link href={signIn} className="w-fit text-[13px] font-semibold text-fg-accent hover:underline">
            Sign in
          </Link>
        )}
      </div>
    </div>
  );
}

function MediaCard({ rec, duration, signedIn }: { rec: Rec; duration: number; signedIn: boolean }) {
  const api = usePlayerApi();
  const phone = useMediaQuery("(max-width: 639px)");
  const closed = rec.closed.includes("media");
  if (!rec.media && !closed) return null; // nothing to play: a transcript on its own
  const m = rec.media;
  const title = rec.media_kind === "video" ? "Video" : "Audio";
  if (!m) {
    return (
      <Card title={title}>
        <ClosedNote part="media" rec={rec} signedIn={signedIn} />
      </Card>
    );
  }
  const t = rec.transcript;
  const lanes: WaveLane[] = t?.speakers.length
    ? t.speakers.map((s) => ({ key: s.key, name: s.name, color: s.color }))
    : [{ key: "all", name: title, color: "var(--fg-muted)" }];
  const segs: Segment[] = (t?.segments ?? []).map((s, idx) => ({
    idx,
    t0: s.t0,
    t1: s.t1,
    speaker: s.s ?? null,
    text: s.text,
    emotion: null,
    event: null,
  }));
  return (
    <Card title={title}>
      {m.kind === "video" ? (
        <video
          ref={api.attach}
          src={m.url}
          poster={m.poster ?? undefined}
          playsInline
          preload="metadata"
          className="aspect-video w-full cursor-pointer rounded-sm bg-black object-contain"
          onClick={() => api.toggle()}
          aria-label={`Video: ${rec.title ?? "recording"}`}
        />
      ) : (
        <audio ref={api.attach} src={m.url} preload="metadata" className="hidden" />
      )}
      <Waveform
        mode={t?.speakers.length ? "wave" : "unsorted"}
        lanes={lanes}
        segments={segs}
        envelope={(m.envelope as number[] | null | undefined) ?? null}
        durationMs={duration}
        chapters={(rec.chapters ?? []).map((c) => ({ t0: c.t0, title: c.title ?? "" }))}
        ticks={[]}
        valueText={(ms) => `${tc(ms)} of ${tc(duration)}`}
        progress={1}
        compact={phone}
        bars={phone ? 60 : 120}
      />
      <div className="flex flex-wrap items-center gap-2">
        <SkipButton dir={-1} />
        <PlayButton />
        <SkipButton dir={1} />
        <TimeReadout className="ml-1" />
        <span className="flex-1" />
        <SpeedMenu />
        <span className="max-sm:hidden">
          <VolumeControl />
        </span>
      </div>
    </Card>
  );
}

function TranscriptCard({ rec, signedIn }: { rec: Rec; signedIn: boolean }) {
  const t = rec.transcript;
  if (!t) {
    return rec.closed.includes("transcript") ? (
      <Card title="Transcript">
        <ClosedNote part="transcript" rec={rec} signedIn={signedIn} />
      </Card>
    ) : null;
  }
  return <Transcript rec={rec} t={t} />;
}

function Transcript({ rec, t }: { rec: Rec; t: NonNullable<Rec["transcript"]> }) {
  const api = usePlayerApi();
  const { time } = usePlayerState();
  const [query, setQuery] = useState("");
  const [at, setAt] = useState(0);
  const lines = useRef<(HTMLLIElement | null)[]>([]);
  const hits = useMemo(() => findLines(t.segments, query), [t.segments, query]);
  const speakers = useMemo(() => new Map(t.speakers.map((s) => [s.key, s])), [t.speakers]);
  const current = rec.media ? lineAt(t.segments, time) : -1;
  const go = (n: number) => {
    if (!hits.length) return;
    const k = (n + hits.length) % hits.length;
    setAt(k);
    lines.current[hits[k]]?.scrollIntoView({ block: "center", behavior: "smooth" });
  };
  return (
    <Card title="Transcript" extra={<span className="text-[12px] text-fg-muted">{t.segments.length} lines</span>}>
      <div className="flex items-center gap-2">
        <div className="min-w-0 flex-1">
          <SearchInput
            aria-label="Find in the transcript"
            placeholder="Find in the transcript"
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
              {hits.length ? `${at + 1} of ${hits.length}` : "No lines match"}
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
      <ol className="flex flex-col">
        {t.segments.map((s, i) => {
          const who = s.s ? speakers.get(s.s) : undefined;
          const hit = query.trim() && hits.includes(i);
          return (
            <li
              key={i}
              ref={(el) => {
                lines.current[i] = el;
              }}
              className={cn(
                "grid grid-cols-[52px_minmax(0,1fr)] gap-x-3 rounded-sm px-2 py-2",
                i === current && "bg-hl",
                hit && hits[at] === i && "outline outline-2 outline-blue-border",
              )}
            >
              {rec.media ? (
                <button
                  type="button"
                  onClick={() => {
                    api.seek(s.t0, { manual: true });
                    api.play();
                  }}
                  aria-label={`Play from ${tc(s.t0)}`}
                  className="tabular h-fit text-left text-[12px] font-medium text-fg-accent hover:underline"
                >
                  {tc(s.t0)}
                </button>
              ) : (
                <span className="tabular text-[12px] text-fg-muted">{tc(s.t0)}</span>
              )}
              <div className="flex min-w-0 flex-col gap-0.5">
                {who && (
                  <span className="flex items-center gap-1.5 text-[12px] font-bold" style={{ color: who.color }}>
                    {who.name}
                  </span>
                )}
                <p className="font-serif text-[15.5px] leading-[1.55] text-fg">
                  {markParts(s.text, query).map((p, k) =>
                    p.hit ? (
                      <mark key={k} className="rounded-[2px] bg-hl-word text-fg">
                        {p.text}
                      </mark>
                    ) : (
                      <span key={k}>{p.text}</span>
                    ),
                  )}
                </p>
              </div>
            </li>
          );
        })}
      </ol>
    </Card>
  );
}

function ChaptersCard({ rec, signedIn }: { rec: Rec; signedIn: boolean }) {
  const api = usePlayerApi();
  if (!rec.chapters) {
    return rec.closed.includes("index") ? (
      <Card title="Chapters">
        <ClosedNote part="index" rec={rec} signedIn={signedIn} />
      </Card>
    ) : null;
  }
  if (!rec.chapters.length) return null;
  return (
    <Card title="Chapters" extra={<span className="text-[12px] text-fg-muted">{rec.chapters.length}</span>}>
      <ol className="flex flex-col gap-0.5">
        {rec.chapters.map((c, i) => (
          <li key={i}>
            <button
              type="button"
              disabled={!rec.media}
              onClick={() => {
                api.seek(c.t0, { manual: true });
                api.play();
              }}
              className="grid w-full grid-cols-[22px_minmax(0,1fr)_auto] items-baseline gap-2 rounded-sm px-2 py-1.5 text-left hover:bg-surface disabled:cursor-default disabled:hover:bg-transparent"
            >
              <span className="tabular text-[12px] font-semibold text-fg-muted">{i + 1}</span>
              <span className="text-[13.5px] font-medium leading-snug text-fg">{c.title || `Chapter ${i + 1}`}</span>
              <span className="tabular text-[12px] text-fg-muted">{tc(c.t0)}</span>
            </button>
          </li>
        ))}
      </ol>
    </Card>
  );
}

function DescriptionCard({ meta }: { meta: Meta }) {
  const rows = descriptionRows(meta);
  if (!rows.length) return null;
  return (
    <Card title="About this recording">
      <dl className="flex flex-col gap-2.5">
        {rows.map((r) => (
          <div key={r.label} className="flex flex-col gap-0.5">
            <dt className="text-[12px] font-semibold text-fg-muted">{r.label}</dt>
            <dd className="text-[13.5px] leading-[1.45] text-fg">
              {r.items.map((it, i) => {
                const href = safeHref(it.href);
                return (
                  <span key={i}>
                    {i > 0 && ", "}
                    {href ? (
                      <a
                        href={href}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="break-words font-medium text-fg-accent hover:underline"
                      >
                        {it.text}
                        <ArrowUpRight aria-hidden className="ml-0.5 inline size-3" />
                      </a>
                    ) : (
                      <span className="break-words">{it.text}</span>
                    )}
                  </span>
                );
              })}
            </dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}

function DownloadsCard({ rec }: { rec: Rec }) {
  const files = rec.transcript?.downloads ?? [];
  if (!files.length) return null;
  return (
    <Card title="Download the transcript">
      <ul className="flex flex-wrap gap-2">
        {files.map((f) => (
          <li key={f.format}>
            <Button asChild size="sm" variant="ghost">
              <a href={f.url} download aria-label={f.label}>
                <Download aria-hidden />
                {f.format.toUpperCase()}
              </a>
            </Button>
          </li>
        ))}
      </ul>
    </Card>
  );
}
