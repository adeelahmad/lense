"use client";

import * as D from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { useRef, type KeyboardEvent, type ReactNode } from "react";

import { cn } from "@/lib/utils";

const FIELDS =
  "[data-autofocus], input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=file]):not(:disabled), textarea:not(:disabled), select:not(:disabled)";
const TYPED = new Set(["text", "search", "email", "url", "tel", "number", "password", ""]);

/** On open, the first field (or one marked data-autofocus) has the focus, not the close button. */
function focusFirstField(e: Event) {
  const box = e.currentTarget as HTMLElement | null;
  const first = box?.querySelector<HTMLElement>(FIELDS);
  if (!first) return; // nothing to type in: the default (the first button) is fine
  e.preventDefault();
  first.focus();
}

/** Enter in a one-line field runs the dialog's main action (its last primary or danger button), unless the field
 * handles Enter itself, is in a form of its own, or the main action can't run yet. */
function enterSubmits(e: KeyboardEvent<HTMLElement>, actions: HTMLElement | null) {
  const t = e.target as HTMLElement;
  if (e.key !== "Enter" || e.defaultPrevented || e.shiftKey || e.altKey || e.nativeEvent.isComposing) return;
  if (!(t instanceof HTMLInputElement) || !TYPED.has(t.type) || t.form || !actions || actions.contains(t)) return;
  if (t.getAttribute("role") === "combobox" || t.getAttribute("aria-expanded") === "true") return;
  const buttons = [
    ...actions.querySelectorAll<HTMLButtonElement>('button[data-variant="primary"], button[data-variant="danger"]'),
  ];
  const main = buttons[buttons.length - 1];
  if (!main || main.disabled || main.getAttribute("aria-disabled") === "true") return;
  e.preventDefault();
  main.click();
}

/** Modal dialog: 24px radius, scrim with a 2px blur, title 700/22. Opens with its first field focused; Enter in a
 * one-line field runs the main action (the last primary or danger button in `actions`). */
export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  actions,
  className,
  wide,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  description?: ReactNode;
  children?: ReactNode;
  actions?: ReactNode;
  className?: string;
  wide?: boolean;
}) {
  const actionsRef = useRef<HTMLDivElement>(null);
  return (
    <D.Root open={open} onOpenChange={onOpenChange}>
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-[100] bg-[var(--scrim)] backdrop-blur-[2px] animate-fade-in" />
        <D.Content
          onOpenAutoFocus={focusFirstField}
          onKeyDown={(e) => enterSubmits(e, actionsRef.current)}
          className={cn(
            "fixed left-1/2 top-1/2 z-[101] flex max-h-[90vh] w-[calc(100vw-32px)] -translate-x-1/2 -translate-y-1/2 flex-col gap-4 overflow-y-auto rounded-xl bg-background p-6 shadow-3 animate-fade-in",
            wide ? "max-w-[720px]" : "max-w-[480px]",
            className,
          )}
        >
          <div className="flex items-start gap-3">
            <D.Title className="flex-1 text-[22px] font-bold leading-tight tracking-[-.01em] text-fg">{title}</D.Title>
            <D.Close
              className="-mr-2 -mt-1 grid size-8 place-items-center rounded-full text-fg-secondary hover:bg-surface-neutral"
              aria-label="Close"
            >
              <X className="size-4" />
            </D.Close>
          </div>
          {description ? (
            <D.Description className="text-[14.5px] leading-normal text-fg-secondary">{description}</D.Description>
          ) : (
            <D.Description className="sr-only">{typeof title === "string" ? title : "Dialog"}</D.Description>
          )}
          {children}
          {actions && (
            <div ref={actionsRef} className="flex flex-wrap justify-end gap-2 pt-1">
              {actions}
            </div>
          )}
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}

/**
 * Side drawer. Slides in from the right over content with no scrim, so the page stays usable; Esc closes it.
 */
export function Drawer({
  open,
  onOpenChange,
  title,
  actions,
  children,
  width = 400,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  width?: number;
}) {
  return (
    <D.Root open={open} onOpenChange={onOpenChange} modal={false}>
      <D.Portal>
        <D.Content
          onInteractOutside={(e) => e.preventDefault()}
          style={{ width }}
          className="fixed bottom-0 right-0 top-16 z-[90] flex max-w-full flex-col border-l border-border bg-background shadow-3 animate-slide-in-right"
        >
          <div className="flex items-center gap-3 border-b border-border px-4 py-3">
            <D.Title className="flex-1 text-[16px] font-bold text-fg">{title}</D.Title>
            {actions}
            <D.Close
              className="grid size-8 place-items-center rounded-full text-fg-secondary hover:bg-surface-neutral"
              aria-label="Close"
            >
              <X className="size-4" />
            </D.Close>
          </div>
          <D.Description className="sr-only">{typeof title === "string" ? title : "Panel"}</D.Description>
          <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}
