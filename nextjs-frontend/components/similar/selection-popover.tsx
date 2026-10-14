"use client";

import { Sparkles, X } from "lucide-react";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { TEXT_MAX, TEXT_MIN, type SimilarBasis } from "@/components/similar/model";
import { useSelectMode } from "@/components/similar/prefs";
import { SimilarResultsView } from "@/components/similar/similar-list";
import { cn } from "@/lib/utils";

const WIDTH = 380;
const SETTLE_MS = 350; // a selection that stays put this long is what was meant

type Picked = { text: string; top: number; bottom: number; x: number };

/** Selected text worth comparing: in the page's content (not the header, navigation or chat), not in a field, and with
 * a word in it. Elements marked data-no-similar opt out. */
export function selectedText(sel: Selection | null, root: Element | null): string | null {
  if (!sel || sel.isCollapsed || !sel.rangeCount || !root) return null;
  const node = sel.getRangeAt(0).commonAncestorContainer;
  const el = node.nodeType === 1 ? (node as Element) : node.parentElement;
  if (!el || !root.contains(el)) return null;
  if (el.closest("input, textarea, select, [contenteditable=true], [data-no-similar], [role=dialog], [role=menu]"))
    return null;
  const text = sel.toString().replace(/\s+/g, " ").trim();
  if (text.length < TEXT_MIN || !/\p{L}{2}/u.test(text)) return null;
  return text.slice(0, TEXT_MAX);
}

/** The resource a page is about (/resources/12), so what's selected in it doesn't find itself. */
export function resourceOf(pathname: string | null): number | null {
  const m = /^\/(?:resources|recordings)\/(\d+)/.exec(pathname ?? "");
  return m ? Number(m[1]) : null;
}

/**
 * Selecting text anywhere in a page (a resource's text or title, a note, a topic or an entity) shows the content most
 * like it in a popover under the selection, with Find more for all of it. On a resource's text the selection toolbar
 * stays above and this goes below. Esc, a click elsewhere or scrolling closes it. In the account menu, "Similar on
 * select: Button" offers a small Similar button instead of opening at once.
 */
export function SimilarOnSelect() {
  const pathname = usePathname();
  const [mode] = useSelectMode();
  const [picked, setPicked] = useState<Picked | null>(null);
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const modeRef = useRef(mode);
  modeRef.current = mode;
  const shown = useRef(false);
  shown.current = picked != null;

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let pressed = false;
    let dismissed: string | null = null; // closed while still selected (a toolbar button keeps it): don't reopen
    const main = () => document.getElementById("main");
    const inside = (t: EventTarget | null) => t instanceof Node && Boolean(box.current?.contains(t));
    const look = () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        if (pressed) return; // still dragging: wait for the button to come up
        const sel = window.getSelection();
        if (sel && sel.rangeCount && inside(sel.getRangeAt(0).commonAncestorContainer)) return;
        const text = selectedText(sel, main());
        if (!text || !sel) {
          dismissed = null;
          return;
        }
        if (text === dismissed) return;
        const r = sel.getRangeAt(0).getBoundingClientRect();
        setPicked((p) => (p?.text === text ? p : { text, top: r.top, bottom: r.bottom, x: r.left + r.width / 2 }));
        setOpen(modeRef.current === "show");
      }, SETTLE_MS);
    };
    const close = () => {
      clearTimeout(timer);
      dismissed = selectedText(window.getSelection(), main());
      setPicked(null);
      setOpen(false);
    };
    const onDown = (e: PointerEvent) => {
      if (inside(e.target)) return;
      pressed = true;
      close();
    };
    const onUp = () => {
      pressed = false;
      look();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    const onScroll = (e: Event) => {
      if (shown.current && !inside(e.target)) close(); // it would float away from the words
    };
    document.addEventListener("selectionchange", look);
    document.addEventListener("pointerdown", onDown, true);
    document.addEventListener("pointerup", onUp, true);
    document.addEventListener("keydown", onKey);
    window.addEventListener("scroll", onScroll, true);
    const onResize = () => shown.current && close();
    window.addEventListener("resize", onResize);
    return () => {
      clearTimeout(timer);
      document.removeEventListener("selectionchange", look);
      document.removeEventListener("pointerdown", onDown, true);
      document.removeEventListener("pointerup", onUp, true);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onResize);
    };
  }, []);

  // a new page: what was selected on the last one is gone
  useEffect(() => {
    setPicked(null);
    setOpen(false);
  }, [pathname]);

  if (!picked) return null;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const narrow = vw < 640;
  const below = vh - picked.bottom > 260 || picked.top < 300;
  const left = narrow ? 8 : Math.max(8, Math.min(picked.x - WIDTH / 2, vw - WIDTH - 8));
  const exclude = resourceOf(pathname);
  const basis: SimilarBasis = { text: picked.text, exclude };
  const quote = picked.text.length > 70 ? `${picked.text.slice(0, 67)}…` : picked.text;
  const place = below ? { top: Math.min(picked.bottom + 12, vh - 120) } : { bottom: vh - picked.top + 12 };

  if (!open)
    return (
      <div
        ref={box}
        className="fixed z-70"
        style={{ left: narrow ? undefined : picked.x - 44, right: narrow ? 8 : undefined, ...place }}
      >
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="inline-flex h-8 items-center gap-1.5 rounded-pill border border-border bg-background px-3 text-[12.5px] font-semibold text-fg-accent shadow-2 hover:bg-blue-surface"
        >
          <Sparkles aria-hidden className="size-3.5" />
          Similar
        </button>
      </div>
    );

  return (
    <div
      ref={box}
      role="dialog"
      aria-label="Similar content"
      data-no-similar
      className={cn(
        "fixed z-70 flex max-h-[min(520px,calc(100vh-96px))] flex-col gap-2 overflow-hidden rounded-md border border-border bg-background p-3.5 shadow-3 animate-fade-in",
      )}
      style={{ left, width: narrow ? vw - 16 : WIDTH, ...place }}
    >
      <div className="flex items-start gap-2">
        <Sparkles aria-hidden className="mt-0.5 size-4 shrink-0 text-blue" />
        <div className="min-w-0 flex-1 text-[13px] leading-snug text-fg-secondary">
          Similar to <span className="font-serif italic text-fg">“{quote}”</span>
        </div>
        <button
          type="button"
          aria-label="Close"
          onClick={() => {
            setPicked(null);
            setOpen(false);
          }}
          className="grid size-6 shrink-0 place-items-center rounded-full text-fg-muted hover:bg-surface-neutral hover:text-fg"
        >
          <X className="size-3.5" />
        </button>
      </div>
      <div className="min-h-0 overflow-y-auto">
        <SimilarResultsView basis={basis} limit={5} compact onOpen={() => setPicked(null)} />
      </div>
    </div>
  );
}
