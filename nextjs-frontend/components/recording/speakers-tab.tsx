"use client";

import { Play } from "lucide-react";
import Link from "next/link";
import { useState, type FormEvent } from "react";

import { usePlayerApi, usePlayerState } from "@/components/player/media";
import { useRec } from "@/components/recording/context";
import { useRecordingActions, useSpeakerDirectory } from "@/components/recording/hooks";
import { speakerStats } from "@/components/recording/model";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { EmptyState } from "@/components/ui/states";
import { initials, tc } from "@/lib/format";
import { needRole } from "@/lib/hooks/session";

function methodLine(method: string | null | undefined, score: number | null | undefined): string {
  switch (method) {
    case "voice":
      return `Matched to the registry${score != null ? ` · ${score.toFixed(2)}` : ""} · auto`;
    case "label":
      return "Named in the imported transcript";
    case "new":
      return "New voice in this namespace";
    default:
      return method ? `Matched by ${method}` : "Speaker";
  }
}

/**
 * Speakers tab (R2): talk-time bar, one card per speaker (match, talk time, words/min, turns, Rename), and a gold
 * review card when the voice match wasn't sure who someone is.
 */
export function SpeakersTab() {
  const { rec, model, ns, canEdit, id } = useRec();
  const api = usePlayerApi();
  const { hasMedia } = usePlayerState();
  const dir = useSpeakerDirectory(ns);
  const { mergeSpeaker } = useRecordingActions(id);
  const stats = speakerStats(rec.stats);
  const colorOf = (sid: number | null) => model.speakers.find((s) => s.id === sid)?.color ?? "var(--text-muted)";
  const people = [...(rec.speakers ?? [])].sort((a, b) => (model.speakers.find((s) => s.id === a.id)?.index ?? 99) - (model.speakers.find((s) => s.id === b.id)?.index ?? 99));
  const reviews = people
    .map((p) => ({ p, s: [...(dir.data?.speakers.find((d) => d.id === p.id)?.suggestions ?? [])].sort((a, b) => b.score - a.score)[0] }))
    .filter((x) => x.s);

  if (!people.length)
    return (
      <EmptyState title="No speakers yet" className="py-10">
        Speakers come from the transcript&apos;s labels or from the Diarize step, which separates voices and matches them to this namespace&apos;s registry.
      </EmptyState>
    );

  const firstLine = (sid: number) => model.segments.find((s) => s.speaker === `s${sid}`);

  return (
    <>
      {stats.length > 0 && (
        <div className="flex h-2.5 gap-0.5 overflow-hidden rounded-pill" role="img" aria-label={`Talk time: ${stats.map((s) => `${s.name} ${Math.round(s.share * 100)}%`).join(", ")}`}>
          {stats.map((s, i) => (
            <span key={i} style={{ flex: Math.max(s.share, 0.005), background: colorOf(s.id) }} />
          ))}
        </div>
      )}
      {people.map((p) => {
        const st = stats.find((s) => s.id === p.id);
        return <SpeakerCard key={p.id} id={p.id} name={p.name} color={colorOf(p.id)} sub={methodLine(p.method, p.score)} stat={st} canEdit={canEdit} ns={ns} recId={id} />;
      })}
      {reviews.map(({ p, s }) => {
        const line = firstLine(p.id);
        return (
          <section key={p.id} className="flex flex-col gap-2.5 rounded-md border border-gold-border bg-gold-surface p-3.5" aria-label={`${p.name} needs review`}>
            <div className="flex items-center gap-2">
              <span aria-hidden className="size-[9px] rotate-45 rounded-[1px] bg-gold" />
              <h3 className="text-[13.5px] font-bold leading-tight text-fg">{p.name} needs review</h3>
            </div>
            <p className="text-[13px] leading-normal text-fg-strong">
              {line ? `${tc(line.t0)} and the rest of ` : ""}
              <b>{p.name}</b>&apos;s lines may be <b>{s!.name}</b> — the voice match is <b>unsure ({s!.score.toFixed(2)})</b>. Confirming merges {p.name} into {s!.name} across {ns} (with undo).
            </p>
            <div className="flex flex-wrap gap-2">
              {line && (
                <Button
                  size="sm"
                  variant="primary"
                  icon={<Play />}
                  onClick={() => {
                    api.seek(line.t0, { manual: true });
                    if (hasMedia) api.play();
                  }}
                >
                  {hasMedia ? "Play" : "Go to"} {tc(line.t0)}
                </Button>
              )}
              <Button
                size="sm"
                variant="approve"
                disabled={!canEdit || mergeSpeaker.isPending}
                disabledReason={!canEdit ? needRole("editor", ns) : undefined}
                onClick={() => mergeSpeaker.mutate({ sid: p.id, into: s!.id })}
              >
                It&apos;s {s!.name}
              </Button>
              <Button asChild size="sm" variant="secondary">
                <Link href={`/speakers/${p.id}`}>Review in Speakers</Link>
              </Button>
            </div>
          </section>
        );
      })}
    </>
  );
}

function SpeakerCard({
  id,
  name,
  color,
  sub,
  stat,
  canEdit,
  ns,
  recId,
}: {
  id: number;
  name: string;
  color: string;
  sub: string;
  stat?: ReturnType<typeof speakerStats>[number];
  canEdit: boolean;
  ns: string | null;
  recId: number;
}) {
  const [renaming, setRenaming] = useState(false);
  const [value, setValue] = useState(name);
  const { renameSpeaker } = useRecordingActions(recId);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    renameSpeaker.mutate({ sid: id, name: value.trim() }, { onSuccess: () => setRenaming(false) });
  };
  return (
    <section className="flex flex-col gap-3 rounded-md border border-border p-3.5" aria-label={name}>
      <div className="flex items-center gap-2.5">
        <span aria-hidden className="grid size-[30px] shrink-0 place-items-center rounded-full text-[12px] font-bold text-white" style={{ background: color }}>
          {initials(name)}
        </span>
        {renaming ? (
          <form onSubmit={submit} className="flex min-w-0 flex-1 items-center gap-2">
            <Input value={value} onChange={(e) => setValue(e.target.value)} aria-label={`New name for ${name}`} className="h-8 text-[13.5px]" autoFocus maxLength={80} />
            <Button type="submit" size="xs" variant="primary" disabled={renameSpeaker.isPending}>
              Save
            </Button>
            <Button size="xs" variant="ghost" onClick={() => setRenaming(false)}>
              Cancel
            </Button>
          </form>
        ) : (
          <>
            <div className="min-w-0 flex-1">
              <Link href={`/speakers/${id}`} className="block truncate text-[14px] font-bold leading-tight text-fg hover:underline">
                {name}
              </Link>
              <div className="text-[12px] leading-snug text-fg-muted">{sub}</div>
            </div>
            <Button variant="ghost" size="sm" disabled={!canEdit} disabledReason={needRole("editor", ns)} onClick={() => setRenaming(true)}>
              Rename
            </Button>
          </>
        )}
      </div>
      {stat && (
        <dl className="tabular m-0 grid grid-cols-4 gap-2">
          {[
            ["Talk time", `${Math.round(stat.share * 100)}%`],
            ["Duration", tc(stat.talkMs)],
            ["Words/min", stat.wpm ? String(Math.round(stat.wpm)) : "—"],
            ["Turns", String(stat.turns)],
          ].map(([k, v]) => (
            <div key={k} className="flex flex-col-reverse gap-[3px]">
              <dt className="text-[11.5px] leading-tight text-fg-muted">{k}</dt>
              <dd className="m-0 text-[16px] font-bold leading-none text-fg">{v}</dd>
            </div>
          ))}
        </dl>
      )}
    </section>
  );
}
