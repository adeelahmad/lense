"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AudioLines, ChevronLeft, Play, Waypoints } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { Entities, Speakers } from "@/app/openapi-client";
import { hoursShort } from "@/components/chat/scope";
import { useRecordingIndex, useSpeakerDirectory } from "@/components/search/data";
import { InlinePlayerBar, useInlinePlayer } from "@/components/search/player";
import { isUnnamed, monthLabel, speakerTone, talkByMonth, talkTime } from "@/components/speakers/format";
import { ClipRow, SpeakerAvatar, useNamespaceSpeakers, useSpeakerClips } from "@/components/speakers/parts";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input } from "@/components/ui/field";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { count } from "@/lib/format";
import { needRole, useArchive } from "@/lib/hooks/session";

function Stat({ v, k }: { v: string; k: string }) {
  return (
    <div className="flex flex-col gap-1 rounded-md bg-surface p-3">
      <b className="tabular text-[20px] font-bold leading-none text-fg">{v}</b>
      <span className="text-[12px] text-fg-muted">{k}</span>
    </div>
  );
}

/** Talk time per month as bars in the speaker's colour, with the values written on them. */
function MonthBars({ months, color }: { months: { month: string; ms: number }[]; color: string }) {
  const max = Math.max(1, ...months.map((m) => m.ms));
  const label = `Talk time per month, ${months.map((m) => `${monthLabel(m.month)} ${hoursShort(m.ms)}`).join(", ")}`;
  return (
    <div className="flex flex-col gap-2">
      <h3 className="text-[13px] font-bold text-fg">Talk time per month</h3>
      <div role="img" aria-label={label} className="flex h-[120px] items-end gap-2.5 border-b border-border pt-2">
        {months.map((m) => (
          <span key={m.month} className="flex h-full flex-1 flex-col items-center justify-end gap-1.5">
            <span className="tabular text-[11px] font-medium text-fg-secondary">{m.ms ? hoursShort(m.ms) : ""}</span>
            <span
              className="w-full rounded-t-[4px]"
              style={{
                height: `${m.ms ? Math.max(3, (m.ms / max) * 85) : 0}%`,
                background: color,
              }}
            />
          </span>
        ))}
      </div>
      <div className="flex gap-2.5" aria-hidden>
        {months.map((m) => (
          <span key={m.month} className="flex-1 text-center text-[11.5px] font-medium text-fg-muted">
            {monthLabel(m.month)}
          </span>
        ))}
      </div>
    </div>
  );
}

function RenameDialog({
  id,
  current,
  open,
  onOpenChange,
}: {
  id: number;
  current: string;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const [name, setName] = useState(current);
  const save = useMutation({
    mutationFn: () =>
      data(
        Speakers.renameSpeaker({
          client,
          path: { sid: id },
          body: { name: name.trim() },
        }),
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["speakers"] });
      qc.invalidateQueries({ queryKey: ["graph"] });
      toast({
        title: name.trim() ? `Renamed to ${name.trim()}` : "Back to the automatic label",
        tone: "green",
      });
      onOpenChange(false);
    },
  });
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Rename speaker"
      description="The name shows everywhere this voice speaks in this namespace. Leave it empty to go back to the automatic label."
      actions={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="primary" onClick={() => save.mutate()} disabled={save.isPending}>
            {save.isPending ? "Saving…" : "Save"}
          </Button>
        </>
      }
    >
      <Field label="Name" error={save.isError ? save.error.message : undefined}>
        {({ id: fid, invalid }) => (
          <Input
            id={fid}
            value={name}
            invalid={invalid}
            onChange={(e) => setName(e.target.value)}
            maxLength={80}
            autoFocus
          />
        )}
      </Field>
    </Dialog>
  );
}

/** SP4: one speaker — stats, talk time per month, the topics they raise, representative clips. */
export function SpeakerProfile({ id }: { id: number }) {
  const client = useApiClient();
  const { can } = useArchive();
  const dir = useSpeakerDirectory();
  const me = dir.speakers.find((s) => s.id === id);
  const ns = me?.namespace ?? null;
  const nsDir = useNamespaceSpeakers(ns);
  const index = useRecordingIndex();
  const player = useInlinePlayer();
  const [renaming, setRenaming] = useState(false);
  const {
    clips,
    isLoading: clipsLoading,
    recordings,
  } = useSpeakerClips(me ? id : null, {
    recordings: 3,
    perRecording: 1,
    max: 3,
  });
  const topics = useQuery({
    queryKey: ["entities", { speaker: id, ns }],
    queryFn: () =>
      data(
        Entities.listEntities({
          client,
          query: { speaker: id, namespaces: ns ?? undefined, limit: 12 },
        }),
      ),
    enabled: Boolean(ns),
    staleTime: 60_000,
  });

  const recs = useMemo(() => recordings.data ?? [], [recordings.data]);
  const months = useMemo(() => talkByMonth(recs, 6), [recs]);
  const share = useMemo(() => {
    const total = recs.reduce((a, r) => a + (index.byId.get(r.id)?.duration_ms ?? 0), 0);
    const talk = recs.reduce((a, r) => a + (r.talk_ms ?? 0), 0);
    return total > 0 ? Math.round((talk / total) * 100) : null;
  }, [recs, index.byId]);
  const links = (nsDir.data?.links ?? []).filter((l) => l.a === id || l.b === id);

  if (dir.isLoading && !me)
    return (
      <div className="flex flex-col gap-4 px-4 py-6 md:px-6" aria-busy="true" aria-label="Loading speaker">
        <Skeleton className="h-12 w-64" />
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  if (!me)
    return (
      <div className="px-4 py-6 md:px-6">
        <EmptyState
          icon={<AudioLines />}
          title="This speaker isn’t here"
          actions={
            <Button asChild variant="secondary">
              <Link href="/settings/speakers">All speakers</Link>
            </Button>
          }
        >
          They may have been merged into someone else, or they’re in a namespace you can’t read.
        </EmptyState>
      </div>
    );

  const color = speakerTone(me.id);
  const canEdit = can("editor", ns);
  const linkText = links
    .map((l) => (l.a === id ? `“${l.b_name}” in ${l.b_ns}` : `“${l.a_name}” in ${l.a_ns}`))
    .join(", ");
  return (
    <div className="flex flex-col gap-4 px-4 py-6 md:px-6">
      <Link
        href={`/settings/speakers?ns=${encodeURIComponent(ns ?? "")}`}
        className="inline-flex w-fit items-center gap-1 text-[13px] font-semibold text-fg-secondary hover:text-fg"
      >
        <ChevronLeft className="size-4" aria-hidden /> Speakers in {ns}
      </Link>
      <div className="grid gap-6 rounded-lg border border-border px-4 py-5 md:px-6 lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-4">
          <div className="flex flex-wrap items-center gap-3.5">
            <SpeakerAvatar s={me} size={52} />
            <span className="flex min-w-0 flex-1 flex-col gap-1">
              <h1 className="text-[24px] font-bold leading-tight text-fg">
                {me.display}
                {isUnnamed(me) && (
                  <span className="ml-2 align-middle text-[12px] font-semibold text-gold-dark">unnamed</span>
                )}
              </h1>
              <span className="text-[13px] text-fg-muted">
                {ns} · {me.has_voice ? "voiceprint ✓" : "no voiceprint"}
                {linkText && ` · linked: ${linkText}`}
              </span>
            </span>
            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                variant="secondary"
                disabled={!canEdit}
                disabledReason={needRole("editor", ns)}
                onClick={() => setRenaming(true)}
              >
                Rename
              </Button>
              <Button asChild size="sm" variant="ghost">
                <Link href={`/graph?focus=s${me.id}`}>
                  <Waypoints /> Graph
                </Link>
              </Button>
              <Button
                asChild={canEdit}
                size="sm"
                variant="ghost"
                disabled={!canEdit}
                disabledReason={needRole("editor", ns)}
                icon={canEdit ? undefined : <Play />}
              >
                {canEdit ? (
                  <Link href={`/batches/new?speaker=${me.id}&ns=${encodeURIComponent(ns ?? "")}`}>
                    <Play /> Run on {count(me.recordings)}
                  </Link>
                ) : (
                  `Run on ${count(me.recordings)}`
                )}
              </Button>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
            <Stat v={talkTime(me.talk_ms)} k="talk time" />
            <Stat v={count(me.recordings)} k={me.recordings === 1 ? "recording" : "recordings"} />
            <Stat v={count(me.segments)} k="turns" />
            <Stat v={share != null ? `${share}%` : "—"} k="share when present" />
          </div>
          {recordings.isLoading ? (
            <Skeleton className="h-[150px] w-full" />
          ) : (
            <MonthBars months={months} color={color} />
          )}
        </div>
        <div className="flex min-w-0 flex-col gap-4">
          <section aria-labelledby="topics" className="flex flex-col gap-2">
            <h2 id="topics" className="text-[13px] font-bold text-fg">
              Topics {me.display} raises
            </h2>
            {topics.isLoading && <Skeleton className="h-8 w-full" />}
            {topics.isError && <Banner tone="error">{topics.error.message}</Banner>}
            {topics.data && topics.data.items.length === 0 && (
              <p className="m-0 text-[13px] text-fg-secondary">
                No entities found in what they said yet. The Analyze step finds them.
              </p>
            )}
            <ul className="m-0 flex list-none flex-wrap gap-1.5 p-0">
              {(topics.data?.items ?? []).map((e) => (
                <li key={String(e.id)}>
                  <Link
                    href={`/graph?focus=e${e.id}`}
                    className="inline-flex h-8 items-center rounded-pill border border-border px-3 text-[13px] font-medium text-fg hover:border-blue-border hover:bg-blue-surface"
                  >
                    {String(e.name)}
                  </Link>
                </li>
              ))}
            </ul>
          </section>
          <section aria-labelledby="clips" className="flex flex-col gap-2">
            <h2 id="clips" className="text-[13px] font-bold text-fg">
              Representative clips
            </h2>
            {clipsLoading && [0, 1].map((i) => <Skeleton key={i} className="h-14 w-full rounded-[10px]" />)}
            {!clipsLoading && clips.length === 0 && (
              <p className="m-0 text-[13px] text-fg-secondary">No lines to show.</p>
            )}
            {clips.map((c) => (
              <ClipRow key={c.key} clip={c} player={player} showEmotion />
            ))}
            <InlinePlayerBar player={player} />
          </section>
        </div>
      </div>
      <RenameDialog key={me.display} id={me.id} current={me.name ?? ""} open={renaming} onOpenChange={setRenaming} />
    </div>
  );
}
