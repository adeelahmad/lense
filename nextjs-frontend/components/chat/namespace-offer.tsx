"use client";

import { FolderClosed, Plus, X } from "lucide-react";

import type { Suggested } from "@/components/chat/stream";

/** The suggestion a spoken or typed reply names ("use travel", "family trips"), if any. */
export function spokenPick(said: string, items: Suggested[]): Suggested | null {
  const words = ` ${said.toLowerCase().replace(/[^a-z0-9]+/g, " ")} `;
  return items.find((it) => it.name.split(/[-_]/).every((w) => words.includes(` ${w} `))) ?? null;
}

/**
 * When it isn't clear which namespace a conversation over everything is about: the likeliest ones, and new ones to
 * create, to tap (or say). Nothing changes until one is picked.
 */
export function NamespaceOffer({
  items,
  busy,
  onPick,
  onDismiss,
}: {
  items: Suggested[];
  busy: boolean;
  onPick: (it: Suggested) => void;
  onDismiss: () => void;
}) {
  return (
    <div role="group" aria-label="Which namespace is this about?" className="flex flex-wrap items-center gap-1.5">
      <span className="text-[12.5px] font-semibold text-fg-secondary">Which namespace is this about?</span>
      {items.map((it) => (
        <button
          key={it.name}
          type="button"
          disabled={busy}
          onClick={() => onPick(it)}
          className="inline-flex h-7 items-center gap-1 rounded-pill border border-border px-2.5 text-[12.5px] font-semibold text-fg hover:bg-surface-neutral disabled:opacity-60"
        >
          {it.new ? <Plus className="size-3.5" aria-hidden /> : <FolderClosed className="size-3.5" aria-hidden />}
          {it.new ? `New: ${it.name}` : it.name}
        </button>
      ))}
      <button
        type="button"
        aria-label="Keep everything"
        title="Keep the conversation over everything"
        onClick={onDismiss}
        className="grid size-7 place-items-center rounded-full text-fg-muted hover:bg-surface-neutral hover:text-fg"
      >
        <X className="size-3.5" />
      </button>
    </div>
  );
}
