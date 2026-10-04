import type { PageContext } from "@/app/openapi-client/types.gen";

/**
 * Chat on any page: one conversation that follows you from page to page until New chat. What's kept between pages
 * and visits (this browser only): the conversation, whether the panel is open, and whether the page is shared.
 */

export const PAGE_CHAT_KEY = "lens.pageChat";
/** The most of a page's text sent with a question (the server reads up to 12,000 characters). */
export const PAGE_TEXT_MAX = 12000;
/** The most of a highlighted passage kept (the server reads up to 4,000 characters). */
export const SELECTION_MAX = 4000;

export type PageChatState = {
  /** The conversation; null until the first question starts one. */
  chatId: number | null;
  open: boolean;
  /** Send the page's text with each question. */
  sharePage: boolean;
};

export const INITIAL: PageChatState = { chatId: null, open: false, sharePage: true };

export function readState(raw: string | null | undefined): PageChatState {
  if (!raw) return INITIAL;
  try {
    const o = JSON.parse(raw) as Record<string, unknown>;
    return {
      chatId: typeof o.chatId === "number" && Number.isInteger(o.chatId) && o.chatId > 0 ? o.chatId : null,
      open: o.open === true,
      sharePage: o.sharePage !== false,
    };
  } catch {
    return INITIAL;
  }
}

/** Pages where the page chat stays out of the way: the Chat page is the full chat already. */
export function hiddenOn(pathname: string): boolean {
  return pathname === "/chat" || pathname.startsWith("/chat/");
}

/** Whitespace folded (runs of blank lines kept to one), trimmed and cut to `max`. */
export function tidyText(text: string, max: number): string {
  const t = text
    .replace(/\r/g, "")
    .replace(/[ \t\f\v\u00a0]+/g, " ")
    .replace(/ *\n */g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
  return t.length > max ? t.slice(0, max) : t;
}

/** A page's title without the app's suffix: "Library · Lens" → "Library". */
export function pageTitle(docTitle: string): string {
  return docTitle.replace(/\s*[·|–—-]\s*Lens(?: Archive)?\s*$/i, "").trim() || docTitle.trim();
}

/** What goes with a question: where it was asked, the page's text when shared, and the highlighted part. */
export function pageContext(p: {
  url: string;
  title: string;
  text?: string | null;
  selection?: string | null;
}): PageContext {
  const ctx: PageContext = { url: p.url.slice(0, 2000) };
  const title = p.title.trim().slice(0, 300);
  if (title) ctx.title = title;
  const text = p.text ? tidyText(p.text, PAGE_TEXT_MAX) : "";
  if (text) ctx.text = text;
  const sel = p.selection ? tidyText(p.selection, SELECTION_MAX) : "";
  if (sel) ctx.selection = sel;
  return ctx;
}

/** A highlighted passage shortened for a chip or a bubble: “first words … last words”. */
export function quotePreview(text: string, max = 140): string {
  const t = text.replace(/\s+/g, " ").trim();
  if (t.length <= max) return t;
  const half = Math.floor((max - 3) / 2);
  return `${t.slice(0, half).trimEnd()} … ${t.slice(-half).trimStart()}`;
}

/**
 * Whether a selection may be offered to the page chat: some words, outside the chat itself, and not in text that
 * has its own selection toolbar (a recording's transcript or document, marked `data-own-selection`).
 */
export function selectionAllowed(text: string, node: Node | null): boolean {
  if (text.trim().length < 2 || !node) return false;
  const el = node.nodeType === 1 ? (node as Element) : node.parentElement;
  if (!el) return false;
  return !el.closest("[data-page-chat], [data-own-selection], input, textarea, [contenteditable='true']");
}
