"use client";

import { useQueryClient } from "@tanstack/react-query";
import { GitMerge, X } from "lucide-react";
import { useCallback, useState } from "react";

import { Topics } from "@/app/openapi-client";
import type { TopicItem } from "@/components/topics/model";
import { FindBox } from "@/components/ui/find-box";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { plural } from "@/lib/format";

/**
 * Topic curation wherever topics show (the Topics page and its drawer, the graph): merging and deleting at once, with
 * an Undo instead of a confirm, and finding the topic to merge by typing.
 */

export type TopicPick = { id: number; label: string };

/** What shows a topic, so a merge or delete is seen everywhere at once (a topic made from an entity hides it). */
export const TOPIC_KEYS = [["topic"], ["topics"], ["recording-topics"], ["graph"], ["entities"], ["entity"]] as const;

export function useTopicRefresh(): () => void {
  const qc = useQueryClient();
  return useCallback(() => {
    for (const k of TOPIC_KEYS) void qc.invalidateQueries({ queryKey: [...k] });
  }, [qc]);
}

const why = (err: unknown) => (err instanceof ApiError ? err.message : "Please try again.");

/** "Merged Grants into Funding", "Merged 3 topics into Funding". */
export function topicMergeTitle(keep: string, others: string[]): string {
  return others.length === 1
    ? `Merged ${others[0]} into ${keep}`
    : `Merged ${plural(others.length, "topic")} into ${keep}`;
}

/** Topics that match typed text: label first, then another label, then anywhere in the label; the most used first. */
export function findTopics(all: TopicItem[], q: string, exclude: number[] = [], limit = 8): TopicItem[] {
  const k = q.trim().toLowerCase();
  const rank = (t: TopicItem) => {
    if (!k) return 0;
    const l = t.label.toLowerCase();
    if (l.startsWith(k)) return 0;
    if (t.alt.some((a) => a.toLowerCase().startsWith(k))) return 1;
    if (l.includes(k) || t.alt.some((a) => a.toLowerCase().includes(k))) return 2;
    return -1;
  };
  return all
    .filter((t) => !exclude.includes(t.id))
    .map((t) => ({ t, r: rank(t) }))
    .filter((x) => x.r >= 0)
    .sort(
      (a, b) =>
        a.r - b.r ||
        b.t.recordings - a.t.recordings ||
        a.t.label.localeCompare(b.t.label, undefined, { sensitivity: "base" }),
    )
    .slice(0, limit)
    .map((x) => x.t);
}

/** Merging and deleting topics at once, with Undo on the toast. Both return true when it was done. */
export function useTopicChanges() {
  const client = useApiClient();
  const toast = useToast();
  const refresh = useTopicRefresh();
  const [pending, setPending] = useState(false);
  // the toast outlives whatever opened it (a drawer closes, a node goes), so undo isn't tied to a mounted component
  const undo = useCallback(
    async (cid: number, what: string) => {
      try {
        await data(Topics.undoTopicChange({ client, path: { cid } }));
        refresh();
        toast({ title: `${what} undone`, tone: "green" });
      } catch (err) {
        toast({ title: `Couldn’t undo the ${what.toLowerCase()}`, body: why(err), tone: "red" });
      }
    },
    [client, refresh, toast],
  );
  const merge = useCallback(
    async (keep: TopicPick, others: TopicPick[]): Promise<boolean> => {
      const rest = others.filter((o) => o.id !== keep.id);
      if (!rest.length) return false;
      setPending(true);
      try {
        const r = await data(Topics.mergeTopics({ client, body: { keep: keep.id, others: rest.map((o) => o.id) } }));
        refresh();
        toast({
          title: topicMergeTitle(
            keep.label,
            rest.map((o) => o.label),
          ),
          body: "Their labels and assets are this one’s now.",
          tone: "green",
          duration: 10000,
          action: { label: "Undo", onClick: () => void undo(r.undo, "Merge") },
        });
        return true;
      } catch (err) {
        toast({ title: "Couldn’t merge them", body: why(err), tone: "red" });
        return false;
      } finally {
        setPending(false);
      }
    },
    [client, refresh, toast, undo],
  );
  const remove = useCallback(
    async (t: TopicPick): Promise<boolean> => {
      setPending(true);
      try {
        const r = await data(Topics.deleteTopic({ client, path: { tid: t.id } }));
        refresh();
        toast({
          title: `Deleted ${t.label}`,
          body: "Its narrower topics moved up, and assets aren’t about it any more.",
          tone: "green",
          duration: 10000,
          action: { label: "Undo", onClick: () => void undo(r.undo, "Delete") },
        });
        return true;
      } catch (err) {
        toast({ title: "Couldn’t delete it", body: why(err), tone: "red" });
        return false;
      } finally {
        setPending(false);
      }
    },
    [client, refresh, toast, undo],
  );
  return { merge, remove, pending };
}

/** Find a topic of the vocabulary by typing: arrow keys move, Enter picks, Escape leaves. */
export function TopicFind({
  all,
  exclude = [],
  onPick,
  onCancel,
  placeholder = "Find a topic…",
  label,
  autoFocus,
  busy,
}: {
  all: TopicItem[];
  exclude?: number[];
  onPick: (t: TopicPick) => void;
  onCancel?: () => void;
  placeholder?: string;
  label: string;
  autoFocus?: boolean;
  busy?: boolean;
}) {
  const [q, setQ] = useState("");
  const items = findTopics(all, q, exclude).map((t) => ({
    id: t.id,
    name: t.label,
    meta: plural(t.recordings, "asset"),
  }));
  const typed = q.trim();
  return (
    <FindBox
      q={q}
      onQ={setQ}
      items={items}
      empty={typed ? `No topic is called “${typed}”.` : "No other topics here."}
      onPick={(t) => onPick({ id: t.id, label: t.name })}
      onCancel={onCancel}
      placeholder={placeholder}
      label={label}
      autoFocus={autoFocus}
      busy={busy}
    />
  );
}

/** "Merge another topic into this one": a find box; the one picked is merged at once, with Undo. */
export function TopicMergeInto({
  keep,
  all,
  open,
  onOpenChange,
}: {
  keep: TopicPick;
  all: TopicItem[];
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const { merge, pending } = useTopicChanges();
  if (!open) return null;
  return (
    <section
      aria-label={`Merge into ${keep.label}`}
      className="flex flex-col gap-2 rounded-md border border-blue-border bg-blue-surface p-3"
    >
      <div className="flex items-center gap-2">
        <GitMerge aria-hidden className="size-4 text-blue" />
        <h3 className="m-0 min-w-0 flex-1 text-[13px] font-bold text-fg-strong">
          Merge into <span className="text-fg">{keep.label}</span>
        </h3>
        <button
          type="button"
          aria-label="Stop merging"
          onClick={() => onOpenChange(false)}
          className="grid size-7 place-items-center rounded-full text-fg-muted hover:bg-background hover:text-fg"
        >
          <X className="size-3.5" />
        </button>
      </div>
      <p className="m-0 text-[12.5px] text-fg-secondary">
        Pick the topic that means the same. Its labels and recordings become {keep.label}’s, and you can undo it.
      </p>
      <TopicFind
        all={all}
        exclude={[keep.id]}
        label={`Topic to merge into ${keep.label}`}
        placeholder="Type a label…"
        autoFocus
        busy={pending}
        onCancel={() => onOpenChange(false)}
        onPick={(t) => void merge(keep, [t])}
      />
    </section>
  );
}
