"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";

import type { Chat, ChatMessage, ChatNode, Versions } from "@/app/openapi-client/types.gen";
import { IconButton } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** Each message's versions as GET /chats/{id} gives them: it and the others with the same parent and role, oldest first. */
export function versionsOf(tree: ChatNode[]): Map<number, Versions> {
  const groups = new Map<string, number[]>();
  for (const n of tree) {
    const key = `${n.parent ?? ""}:${n.role}`;
    const ids = groups.get(key);
    if (ids) ids.push(n.id);
    else groups.set(key, [n.id]);
  }
  const out = new Map<number, Versions>();
  for (const ids of groups.values()) ids.forEach((id, i) => out.set(id, { index: i + 1, count: ids.length, ids }));
  return out;
}

/** The newest message on `id`'s branch, itself when nothing follows it: where the head goes when its version is shown. */
export function newestBelow(tree: ChatNode[], id: number): number {
  const below = new Set([id]);
  let newest = id;
  // oldest first, so what a message follows comes before it
  for (const n of tree) {
    if (n.parent != null && below.has(n.parent)) {
      below.add(n.id);
      newest = n.id;
    }
  }
  return newest;
}

/** The ids from the first message to `leaf`, following parents. */
export function lineage(tree: ChatNode[], leaf: number): number[] {
  const up = new Map(tree.map((n) => [n.id, n.parent ?? null]));
  const out: number[] = [];
  for (let at: number | null = leaf; at != null && up.has(at) && out.length < up.size; at = up.get(at) ?? null)
    out.push(at);
  return out.reverse();
}

/** The conversation showing the version `id` is in, as PUT /chats/{id}/head will, from messages already seen; null when
 * one of them hasn't been loaded yet. */
export function switched(c: Chat, id: number, seen: Map<number, ChatMessage>): Chat | null {
  const tree = c.tree ?? [];
  const head = newestBelow(tree, id);
  const ids = lineage(tree, head);
  if (!ids.length || !ids.every((i) => seen.has(i))) return null;
  const versions = versionsOf(tree);
  return { ...c, head, messages: ids.map((i) => ({ ...seen.get(i)!, versions: versions.get(i) ?? null })) };
}

/** ‹ 2 / 3 › under a question or an answer with versions: moves between them, as do the arrow keys while it has focus. */
export function VersionSwitch({
  versions,
  what,
  onPick,
  className,
}: {
  versions?: Versions | null;
  what: "question" | "answer";
  onPick?: (id: number) => void;
  className?: string;
}) {
  if (!versions || versions.count < 2) return null;
  const { index, count, ids } = versions;
  const go = (i: number) => {
    if (onPick && i >= 1 && i <= count && i !== index) onPick(ids[i - 1]);
  };
  // aria-disabled rather than disabled at either end, so focus (and the arrow keys) stay on the switch
  const arrow = "aria-disabled:cursor-not-allowed aria-disabled:opacity-40 [&_svg]:size-4";
  return (
    <div
      role="group"
      aria-label={`Versions of this ${what}`}
      className={cn("flex items-center gap-0.5 text-[12px] font-medium text-fg-muted", className)}
      onKeyDown={(e) => {
        if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
        e.preventDefault();
        go(e.key === "ArrowLeft" ? index - 1 : index + 1);
      }}
    >
      <IconButton
        label={`Previous version of this ${what}`}
        size={28}
        aria-disabled={!onPick || index <= 1}
        onClick={() => go(index - 1)}
        className={arrow}
      >
        <ChevronLeft />
      </IconButton>
      <span className="tabular-nums">
        {index} / {count}
      </span>
      <IconButton
        label={`Next version of this ${what}`}
        size={28}
        aria-disabled={!onPick || index >= count}
        onClick={() => go(index + 1)}
        className={arrow}
      >
        <ChevronRight />
      </IconButton>
    </div>
  );
}
