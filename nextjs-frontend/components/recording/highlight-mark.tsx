"use client";

import type { MouseEvent, ReactNode } from "react";

import { colourOf, type HighlightRange } from "@/components/recording/comments-model";
import { cn } from "@/lib/utils";

/** A highlighted run of text: marked in its colour, named by its label; a click opens it in the Highlights tab. */
export function HighlightMark({
  hl,
  onOpen,
  children,
}: {
  hl: HighlightRange;
  onOpen?: (id: number) => void;
  children: ReactNode;
}) {
  const click = (e: MouseEvent) => {
    const sel = typeof window !== "undefined" ? window.getSelection() : null;
    if (sel && !sel.isCollapsed) return; // picking words inside it isn't a click on it
    e.stopPropagation();
    onOpen?.(hl.id);
  };
  return (
    <mark
      data-highlight={hl.id}
      title={hl.label ?? undefined}
      onClick={click}
      className={cn("cursor-pointer rounded-[3px] text-fg", colourOf(hl.colour).mark)}
    >
      {children}
    </mark>
  );
}
