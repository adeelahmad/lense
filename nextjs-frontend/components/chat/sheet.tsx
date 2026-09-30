"use client";

import * as D from "@radix-ui/react-dialog";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/** A bottom sheet for phones (citation previews, sources): slides over the page with a scrim; Esc or a tap outside closes it. */
export function BottomSheet({
  open,
  onOpenChange,
  title,
  children,
  className,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <D.Root open={open} onOpenChange={onOpenChange}>
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-[100] bg-[var(--scrim)] animate-fade-in" />
        <D.Content
          className={cn(
            "fixed inset-x-0 bottom-0 z-[101] flex max-h-[85vh] flex-col gap-3 overflow-y-auto rounded-t-xl bg-background px-4 pb-6 pt-2.5 shadow-3 animate-fade-in",
            className,
          )}
        >
          <span aria-hidden className="mx-auto h-1 w-10 shrink-0 rounded-pill bg-border" />
          <D.Title className="sr-only">{title}</D.Title>
          <D.Description className="sr-only">{title}</D.Description>
          {children}
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}
