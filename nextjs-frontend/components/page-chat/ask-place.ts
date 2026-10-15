"use client";

import { useEffect, useLayoutEffect, useState, type RefObject } from "react";

/**
 * Where the floating Ask button sits. It rests in the bottom-right corner, and never on a control people need there:
 * when one is under it and scrolling can't carry it clear (a sticky Import or Save row, or the page's last button
 * with the page at its end), the button rises above it; with no room to rise (the controls reach past the middle of
 * the screen), it steps out of sight until the corner is clear. A transparent probe the button's size, just below it
 * in the stacking order, finds what the button would cover with the browser's own hit testing, so whatever is drawn
 * over the button (a dialog, a drawer, a toast) never moves it.
 */

export type AskPlace = { lift: number; hidden: boolean };
export type Box = { top: number; bottom: number; left: number; right: number; height: number };

export const AT_REST: AskPlace = { lift: 0, hidden: false };
const OUT_OF_SIGHT: AskPlace = { lift: 0, hidden: true };
/** Between the button and the controls it rises above. */
export const ASK_GAP = 12;

/** Things people click or type into. Clickable rows and cards (cursor: pointer) count too. */
const CONTROL = [
  "a[href]",
  "button",
  "input:not([type='hidden'])",
  "select",
  "textarea",
  "summary",
  "label",
  "[contenteditable='true']",
  "[contenteditable='']",
  ...[
    "button",
    "link",
    "checkbox",
    "radio",
    "switch",
    "tab",
    "menuitem",
    "option",
    "slider",
    "combobox",
    "textbox",
  ].map((r) => `[role='${r}']`),
].join(", ");

/**
 * The lift that puts the button clear of the controls under it. `under(lift)` says where the button would be, raised
 * by `lift` pixels, and what it would cover there; it rises above them, and again over anything it then covers, but
 * never past the middle of the screen.
 */
export function placeAsk(under: (lift: number) => { box: Box; blockers: Box[] }, viewHeight: number): AskPlace {
  let lift = 0;
  for (let tries = 0; tries < 4; tries++) {
    const { box, blockers } = under(lift);
    if (!blockers.length) return lift ? { lift, hidden: false } : AT_REST;
    const rise = box.bottom - Math.min(...blockers.map((b) => b.top)) + ASK_GAP;
    if (box.top - rise < viewHeight / 2) return OUT_OF_SIGHT;
    lift += rise;
  }
  return OUT_OF_SIGHT;
}

/** Points spread over a box (inset from its edges), to find what lies under it. */
export function samplePoints(b: Box, cols = 5, rows = 3): [number, number][] {
  const inset = 3;
  const at = (from: number, to: number, i: number, n: number) => from + inset + ((to - from - 2 * inset) * i) / (n - 1);
  const out: [number, number][] = [];
  for (let r = 0; r < rows; r++)
    for (let c = 0; c < cols; c++) out.push([at(b.left, b.right, c, cols), at(b.top, b.bottom, r, rows)]);
  return out;
}

/** The control `el` is, or is part of, if any. */
export function controlAround(el: Element): Element | null {
  const c = el.closest(CONTROL);
  if (c) return c;
  if (getComputedStyle(el).cursor !== "pointer") return null;
  let top = el; // the whole clickable row or card, not the word in it
  while (
    top.parentElement &&
    top.parentElement !== document.body &&
    getComputedStyle(top.parentElement).cursor === "pointer"
  )
    top = top.parentElement;
  return top;
}

const scrolls = (s: CSSStyleDeclaration) => /(auto|scroll|overlay)/.test(s.overflowY);

/** A bar around `el` that stays put as the page scrolls: fixed, or sticky and stuck to the bottom of its scroller. */
export function stuckBar(el: Element): Element | null {
  for (let a: Element | null = el; a && a !== document.body; a = a.parentElement) {
    const s = getComputedStyle(a);
    if (s.position === "fixed") return a;
    if (s.position !== "sticky") continue;
    const offset = parseFloat(s.bottom);
    if (Number.isNaN(offset)) continue; // sticky to the top
    let port = window.innerHeight;
    for (let p = a.parentElement; p && p !== document.body; p = p.parentElement)
      if (scrolls(getComputedStyle(p))) {
        port = Math.min(port, p.getBoundingClientRect().bottom);
        break;
      }
    if (a.getBoundingClientRect().bottom >= port - offset - 2) return a;
  }
  return null;
}

/** How far scrolling down (the page, and anything scrolling inside it) could still carry `el` up the screen. */
export function scrollRoom(el: Element): number {
  let room = 0;
  for (let a = el.parentElement; a && a !== document.body && a !== document.documentElement; a = a.parentElement)
    if (a.scrollHeight > a.clientHeight && scrolls(getComputedStyle(a)))
      room += a.scrollHeight - a.clientHeight - a.scrollTop;
  const doc = document.scrollingElement ?? document.documentElement;
  return room + Math.max(0, doc.scrollHeight - doc.clientHeight - doc.scrollTop);
}

const toBox = (r: DOMRect): Box => ({ top: r.top, bottom: r.bottom, left: r.left, right: r.right, height: r.height });

/**
 * The controls `probe` covers where it is now that scrolling can't carry clear of it: the bar each sits in when it has
 * a short one (so the button clears the whole bar), else the control. Big fields and panels (taller than a third of
 * the screen) don't count: the button covers a corner of them, and the rest stays usable.
 */
export function blockersUnder(probe: Element, box: Box): Box[] {
  const view = window.innerHeight;
  const seen = new Set<Element>();
  const out: Box[] = [];
  for (const [x, y] of samplePoints(box)) {
    const stack = document.elementsFromPoint(x, y);
    const i = stack.indexOf(probe);
    const below = i >= 0 ? stack[i + 1] : undefined; // what's drawn just under the button, not over it
    if (!below || below.closest("[data-page-chat], [data-ask-button]")) continue;
    const control = controlAround(below);
    if (!control || seen.has(control)) continue;
    seen.add(control);
    const own = toBox(control.getBoundingClientRect());
    if (own.height > view / 3) continue;
    const bar = stuckBar(control);
    if (!bar && scrollRoom(control) >= own.bottom - box.top) continue; // scrolling on carries it clear
    const barBox = bar ? toBox(bar.getBoundingClientRect()) : null;
    out.push(barBox && barBox.height <= view / 3 ? barBox : own);
  }
  return out;
}

/** Measures where the button goes now, with the probe (normally inert) briefly hit-testable at each spot it tries. */
export function measureAsk(probe: HTMLElement, button: HTMLElement): AskPlace {
  if (typeof document.elementsFromPoint !== "function") return AT_REST;
  probe.style.width = `${button.offsetWidth}px`;
  probe.style.height = `${button.offsetHeight}px`;
  probe.style.pointerEvents = "auto";
  try {
    return placeAsk((lift) => {
      probe.style.translate = lift ? `0 ${-lift}px` : "";
      const box = toBox(probe.getBoundingClientRect());
      return { box, blockers: blockersUnder(probe, box) };
    }, window.innerHeight);
  } finally {
    probe.style.pointerEvents = "none";
    probe.style.translate = "";
  }
}

const useIsoLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

/** The button's place, measured again (once a frame at most) as the page scrolls, resizes or changes. */
export function useAskPlace(probe: RefObject<HTMLElement | null>, button: RefObject<HTMLElement | null>): AskPlace {
  const [place, setPlace] = useState<AskPlace>(AT_REST);
  useIsoLayoutEffect(() => {
    let frame = 0;
    const measure = () => {
      frame = 0;
      const p = probe.current;
      const b = button.current;
      if (!p || !b) return;
      const next = measureAsk(p, b);
      setPlace((now) => (now.lift === next.lift && now.hidden === next.hidden ? now : next));
    };
    const soon = () => {
      if (!frame) frame = requestAnimationFrame(measure);
    };
    measure();
    window.addEventListener("scroll", soon, { capture: true, passive: true });
    window.addEventListener("resize", soon);
    document.addEventListener("transitionend", soon, true);
    document.addEventListener("animationend", soon, true);
    // the page changing under it (a sticky row appearing, a dialog closing), but not the button moving itself
    const mine = (n: Node) => n === probe.current || Boolean(button.current?.contains(n));
    const changes = new MutationObserver((records) => {
      if (records.some((r) => !mine(r.target))) soon();
    });
    changes.observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["class", "style", "hidden", "open"],
    });
    const sizes = typeof ResizeObserver === "function" ? new ResizeObserver(soon) : null;
    sizes?.observe(document.body);
    return () => {
      if (frame) cancelAnimationFrame(frame);
      window.removeEventListener("scroll", soon, { capture: true });
      window.removeEventListener("resize", soon);
      document.removeEventListener("transitionend", soon, true);
      document.removeEventListener("animationend", soon, true);
      changes.disconnect();
      sizes?.disconnect();
    };
  }, [probe, button]);
  return place;
}
