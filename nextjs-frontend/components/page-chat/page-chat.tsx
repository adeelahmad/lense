"use client";

import { useQueryClient } from "@tanstack/react-query";
import { ArrowUp, FileText, Maximize2, MessagesSquare, Plus, Quote, Square, X } from "lucide-react";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { useSession } from "next-auth/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Chats } from "@/app/openapi-client";
import type { ChatMessage, Passage, SharedContext } from "@/app/openapi-client/types.gen";
import { NO_ANSWER } from "@/components/chat/cite";
import { CitationChip } from "@/components/chat/citation";
import { useChat, useLlmStatus, useStopAnswer } from "@/components/chat/data";
import { RichText } from "@/components/chat/rich-text";
import { ToolSteps } from "@/components/chat/steps";
import { applyEvent, newTurn, savedSteps, type ToolStep, type TurnState } from "@/components/chat/stream";
import {
  hiddenOn,
  INITIAL,
  PAGE_CHAT_KEY,
  pageContext,
  pageTitle,
  quotePreview,
  readState,
  selectionAllowed,
  SELECTION_MAX,
  type PageChatState,
} from "@/components/page-chat/page-chat-model";
import { Button, IconButton } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/states";
import { Tooltip } from "@/components/ui/tooltip";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { SSEError, streamSSE } from "@/lib/api/sse";
import { cn } from "@/lib/utils";

/** The page's own text: what's in the main area, without the page chat. */
function readPageText(): string {
  const main = document.getElementById("main");
  if (!main) return "";
  return main.innerText ?? main.textContent ?? "";
}

function usePersisted(): [PageChatState, (f: (s: PageChatState) => PageChatState) => void] {
  const [state, setState] = useState<PageChatState>(INITIAL);
  const loaded = useRef(false);
  useEffect(() => {
    try {
      setState(readState(localStorage.getItem(PAGE_CHAT_KEY)));
    } catch {
      /* storage unavailable */
    }
    loaded.current = true;
  }, []);
  const update = useCallback((f: (s: PageChatState) => PageChatState) => {
    setState((s) => {
      const next = f(s);
      try {
        localStorage.setItem(PAGE_CHAT_KEY, JSON.stringify(next));
      } catch {
        /* storage unavailable */
      }
      return next;
    });
  }, []);
  return [state, update];
}

function streamError(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof SSEError) {
    if (e.status === 401) return "You were signed out. Sign in again and retry.";
    if (e.status === 403) return "This account can’t start conversations (read-only token).";
    if (e.status === 404) return "This conversation isn’t available any more. Start a new chat.";
    return `The server answered ${e.status}.`;
  }
  return "The chat service didn’t answer.";
}

/**
 * Chat on any page: a panel at the side of every page (a sheet on phones) whose conversation follows you from page to
 * page, and across visits, until New chat. Each question can share the page you're on and text you highlighted on
 * it; highlighting text anywhere offers "Ask about this". The conversation is an ordinary one, also in Chat.
 */
export function PageChat() {
  const pathname = usePathname();
  const search = useSearchParams();
  const [state, update] = usePersisted();
  const [selection, setSelection] = useState<string | null>(null);
  const [focusTick, setFocusTick] = useState(0);
  const hidden = hiddenOn(pathname);

  const open = useCallback(
    (quote?: string) => {
      if (quote) setSelection(quote.slice(0, SELECTION_MAX));
      update((s) => ({ ...s, open: true }));
      setFocusTick((t) => t + 1);
    },
    [update],
  );

  // On a phone the panel covers the page: following a link (a citation, the page a question was asked on) shows it.
  const lastPath = useRef(pathname);
  useEffect(() => {
    if (lastPath.current === pathname) return;
    lastPath.current = pathname;
    if (window.matchMedia?.("(max-width: 1023px)").matches) update((s) => (s.open ? { ...s, open: false } : s));
  }, [pathname, update]);

  if (hidden) return null;
  const url = `${pathname}${search.toString() ? `?${search.toString()}` : ""}`;
  return (
    <>
      <SelectionAsk onAsk={(q) => open(q)} />
      {state.open ? (
        <ChatPanel
          url={url}
          state={state}
          update={update}
          selection={selection}
          clearSelection={() => setSelection(null)}
          focusTick={focusTick}
        />
      ) : (
        <button
          type="button"
          onClick={() => open()}
          aria-label="Chat about this page"
          title="Chat about this page"
          className="fixed bottom-5 right-5 z-[90] inline-flex h-12 items-center gap-2 rounded-pill bg-blue px-4 text-[14px] font-bold text-white shadow-3 transition-transform hover:scale-[1.03] active:scale-[.98] [&_svg]:size-[18px]"
        >
          <MessagesSquare />
          <span className="hidden sm:inline">{state.chatId != null ? "Chat" : "Ask"}</span>
        </button>
      )}
    </>
  );
}

/** Highlighting text on a page offers "Ask about this", above the words. */
function SelectionAsk({ onAsk }: { onAsk: (quote: string) => void }) {
  const [sel, setSel] = useState<{ x: number; y: number; text: string } | null>(null);
  useEffect(() => {
    const onChange = () => {
      const s = window.getSelection();
      if (!s || s.isCollapsed || !s.rangeCount) return setSel(null);
      const range = s.getRangeAt(0);
      const text = s.toString();
      if (!selectionAllowed(text, range.commonAncestorContainer)) return setSel(null);
      const rect = range.getBoundingClientRect();
      setSel({ x: rect.left + rect.width / 2, y: rect.top, text: text.trim() });
    };
    const hide = () => setSel(null);
    document.addEventListener("selectionchange", onChange);
    window.addEventListener("scroll", hide, true);
    return () => {
      document.removeEventListener("selectionchange", onChange);
      window.removeEventListener("scroll", hide, true);
    };
  }, []);
  if (!sel) return null;
  const width = typeof window !== "undefined" ? window.innerWidth : 1200;
  return (
    <button
      type="button"
      data-page-chat
      onMouseDown={(e) => e.preventDefault()} // keep the selection while clicking
      onClick={() => {
        onAsk(sel.text);
        window.getSelection()?.removeAllRanges();
        setSel(null);
      }}
      className="fixed z-[95] inline-flex h-[30px] -translate-x-1/2 -translate-y-[calc(100%+8px)] items-center gap-1.5 whitespace-nowrap rounded-[8px] bg-fg px-2.5 text-[12.5px] font-semibold text-background shadow-3 hover:opacity-90 [&_svg]:size-3.5"
      style={{ left: Math.max(80, Math.min(sel.x, width - 80)), top: Math.max(76, sel.y) }}
    >
      <MessagesSquare /> Ask about this
    </button>
  );
}

function ChatPanel({
  url,
  state,
  update,
  selection,
  clearSelection,
  focusTick,
}: {
  url: string;
  state: PageChatState;
  update: (f: (s: PageChatState) => PageChatState) => void;
  selection: string | null;
  clearSelection: () => void;
  focusTick: number;
}) {
  const client = useApiClient();
  const qc = useQueryClient();
  const { data: session } = useSession();
  const llm = useLlmStatus();
  const chat = useChat(state.chatId);
  const stopAnswer = useStopAnswer();
  const [input, setInput] = useState("");
  const [live, setLive] = useState<{ chatId: number; turn: TurnState; context: SharedContext } | null>(null);
  const [stopping, setStopping] = useState(false);
  const [title, setTitle] = useState("");
  const abort = useRef<AbortController | null>(null);
  const box = useRef<HTMLTextAreaElement>(null);
  const thread = useRef<HTMLDivElement>(null);
  const streaming = live?.turn.status === "streaming";

  // The page's title once it has rendered (titles are set after navigation).
  useEffect(() => {
    const read = () => setTitle(pageTitle(document.title));
    read();
    const t = window.setTimeout(read, 400);
    return () => window.clearTimeout(t);
  }, [url]);
  useEffect(() => {
    requestAnimationFrame(() => box.current?.focus());
  }, [focusTick]);
  useEffect(() => () => abort.current?.abort(), []);
  // A conversation deleted elsewhere (or someone else's after signing in as another account): start afresh.
  useEffect(() => {
    if (chat.error instanceof ApiError && chat.error.status === 404) update((s) => ({ ...s, chatId: null }));
  }, [chat.error, update]);
  const msgs = useMemo(() => chat.data?.messages ?? [], [chat.data]);
  useEffect(() => {
    const el = thread.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [msgs.length, live?.turn.text, live?.turn.steps.length]);

  const close = () => update((s) => ({ ...s, open: false }));
  const newChat = () => {
    abort.current?.abort();
    setLive(null);
    setInput("");
    clearSelection();
    update((s) => ({ ...s, chatId: null }));
    requestAnimationFrame(() => box.current?.focus());
  };

  const send = async () => {
    const question = input.trim();
    if (!question || streaming) return;
    const ctx = pageContext({
      url,
      title,
      text: state.sharePage ? readPageText() : null,
      selection,
    });
    const shown: SharedContext = {
      url: ctx.url,
      title: ctx.title ?? null,
      selection: ctx.selection ?? null,
      page: Boolean(ctx.text),
    };
    setInput("");
    clearSelection();
    setStopping(false);
    let cid = state.chatId;
    const ac = new AbortController();
    abort.current = ac;
    let turn = newTurn(question);
    try {
      if (cid == null) {
        cid = (await data(Chats.createChat({ client, body: {} }))).id;
        const made = cid;
        update((s) => ({ ...s, chatId: made }));
        void qc.invalidateQueries({ queryKey: ["chats"] });
      }
      setLive({ chatId: cid, turn, context: shown });
      for await (const ev of streamSSE(`/api/v1/chats/${cid}/messages`, {
        method: "POST",
        body: { content: question, context: ctx },
        accessToken: session?.accessToken,
        signal: ac.signal,
      })) {
        turn = applyEvent(turn, ev);
        setLive({ chatId: cid, turn, context: shown });
      }
      if (turn.status === "streaming")
        turn =
          turn.messageId != null
            ? { ...turn, status: "done" }
            : { ...turn, status: "error", error: "The answer stopped before it finished." };
    } catch (e) {
      turn = ac.signal.aborted ? { ...turn, status: "stopped" } : { ...turn, status: "error", error: streamError(e) };
      if (cid == null) setInput(question); // the conversation couldn't be started: keep the question
    }
    if (cid == null) return setLive({ chatId: -1, turn, context: shown });
    setLive({ chatId: cid, turn, context: shown });
    await qc.invalidateQueries({ queryKey: ["chat", cid] });
    void qc.invalidateQueries({ queryKey: ["chats"] });
    // a saved answer is in the conversation now; one that wasn't saved stays shown
    if (turn.messageId != null) setLive(null);
  };

  const liveHere = live && (live.chatId === state.chatId || live.chatId === -1) ? live : null;
  // The conversation as saved, without the question (or answer) being shown live if a refetch already has it.
  const saved = liveHere
    ? msgs.filter(
        (m, i) =>
          m.id !== liveHere.turn.messageId &&
          !(i >= msgs.length - 2 && m.role === "user" && m.content.trim() === liveHere.turn.question.trim()),
      )
    : msgs;
  const noModel = llm.known && !llm.configured;
  return (
    <aside
      data-page-chat
      aria-label="Page chat"
      className="fixed inset-0 z-[125] flex flex-col bg-background lg:sticky lg:top-0 lg:z-30 lg:h-screen lg:w-[400px] lg:shrink-0 lg:border-l lg:border-border"
    >
      <div className="flex h-16 shrink-0 items-center gap-1 border-b border-border px-3">
        <MessagesSquare aria-hidden className="ml-1 size-[18px] text-fg-secondary" />
        <h2 className="ml-1.5 min-w-0 flex-1 truncate text-[14px] font-bold text-fg">
          {state.chatId != null ? (chat.data?.title ?? "Chat") : "New chat"}
        </h2>
        <Button
          variant="ghost"
          size="xs"
          icon={<Plus />}
          onClick={newChat}
          disabled={state.chatId == null && !liveHere}
        >
          New chat
        </Button>
        {state.chatId != null && (
          <Tooltip content="Open in Chat">
            <Link
              href={`/chat/${state.chatId}`}
              aria-label="Open in Chat"
              className="inline-grid size-8 shrink-0 place-items-center rounded-full text-fg-secondary transition-colors duration-fast hover:bg-surface-neutral [&_svg]:size-[18px]"
            >
              <Maximize2 />
            </Link>
          </Tooltip>
        )}
        <IconButton label="Close the chat" size={32} onClick={close}>
          <X />
        </IconButton>
      </div>

      <div ref={thread} className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-4 py-4" aria-live="polite">
        {state.chatId != null && chat.isLoading && <Skeleton className="w-3/4" />}
        {!msgs.length && !liveHere && !(state.chatId != null && chat.isLoading) && (
          <div className="flex flex-col gap-2 py-8 text-center">
            <p className="m-0 text-[14px] font-bold text-fg">Ask about this page, or anything in Lens</p>
            <p className="m-0 text-[13px] leading-normal text-fg-secondary">
              The conversation stays with you as you move between pages, until you start a new chat. Highlight text on
              any page and choose Ask about this to quote it.
            </p>
          </div>
        )}
        {saved.map((m) =>
          m.role === "user" ? (
            <Question key={m.id} text={m.content} context={m.context ?? null} />
          ) : (
            <SavedAnswer key={m.id} m={m} />
          ),
        )}
        {liveHere && (
          <>
            <Question text={liveHere.turn.question} context={liveHere.context} />
            <AnswerBody
              text={liveHere.turn.text}
              passages={liveHere.turn.passages ?? []}
              steps={liveHere.turn.steps}
              notice={liveHere.turn.notice}
              error={liveHere.turn.status === "error" ? (liveHere.turn.error ?? "The model didn’t answer.") : null}
              streaming={liveHere.turn.status === "streaming"}
              stopped={liveHere.turn.status === "stopped"}
            />
          </>
        )}
      </div>

      <form
        className="flex shrink-0 flex-col gap-2 border-t border-border px-3 py-3"
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
      >
        {noModel && (
          <p className="m-0 text-[12px] leading-snug text-fg-muted">
            No language model is set up, so answers are the passages that match best. An admin can add one in Settings.
          </p>
        )}
        <div className="flex flex-wrap items-center gap-1.5">
          <button
            type="button"
            aria-pressed={state.sharePage}
            onClick={() => update((s) => ({ ...s, sharePage: !s.sharePage }))}
            title={
              state.sharePage
                ? "The page’s text goes with your question. Click to stop sharing it."
                : "Share this page’s text with your question"
            }
            className={cn(
              "inline-flex h-7 max-w-full items-center gap-1.5 rounded-pill border px-2.5 text-[12px] font-semibold [&_svg]:size-3.5",
              state.sharePage
                ? "border-blue-border bg-blue-surface text-blue-dark"
                : "border-border bg-background text-fg-muted line-through",
            )}
          >
            <FileText />
            <span className="truncate">{title || "This page"}</span>
          </button>
          {selection && (
            <span className="inline-flex h-7 min-w-0 max-w-full items-center gap-1.5 rounded-pill border border-gold-border bg-gold-surface pl-2.5 pr-1 text-[12px] font-semibold text-fg-strong [&_svg]:size-3.5">
              <Quote className="shrink-0" />
              <span className="truncate" title={selection}>
                {quotePreview(selection, 60)}
              </span>
              <button
                type="button"
                onClick={clearSelection}
                aria-label="Remove the highlighted text"
                className="grid size-5 shrink-0 place-items-center rounded-full hover:bg-black/10"
              >
                <X className="!size-3" />
              </button>
            </span>
          )}
        </div>
        <div className="flex items-end gap-2">
          <textarea
            ref={box}
            value={input}
            rows={Math.min(6, Math.max(1, input.split("\n").length))}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                void send();
              }
              if (e.key === "Escape") close();
            }}
            placeholder={selection ? "Ask about the highlighted text…" : "Ask about this page…"}
            aria-label="Ask a question"
            className="max-h-40 min-h-10 flex-1 resize-none rounded-sm border border-border bg-background px-3 py-2 text-[14px] leading-snug text-fg outline-none placeholder:text-fg-muted focus:border-blue focus:shadow-[0_0_0_3px_var(--intent-surface)]"
          />
          {streaming ? (
            <Button
              variant="secondary"
              size="md"
              icon={<Square />}
              onClick={() => {
                setStopping(true);
                stopAnswer(live?.chatId, abort.current);
              }}
              disabled={stopping}
              disabledReason="Stopping after the step it’s on"
              aria-label="Stop the answer"
            >
              {stopping ? "Stopping…" : "Stop"}
            </Button>
          ) : (
            <Button
              type="submit"
              variant="primary"
              size="md"
              icon={<ArrowUp />}
              disabled={!input.trim()}
              aria-label="Send"
            >
              Ask
            </Button>
          )}
        </div>
      </form>
    </aside>
  );
}

/** Where a question was asked from: the page (a link back to it) and the highlighted text it quoted. */
export function SharedFrom({ context, className }: { context: SharedContext | null | undefined; className?: string }) {
  if (!context) return null;
  const href = /^\/(?!\/)/.test(context.url) ? context.url : "/"; // only ever a page in Lens
  return (
    <div className={cn("flex max-w-full flex-col items-end gap-1 self-end", className)}>
      <Link
        href={href}
        className="inline-flex max-w-full items-center gap-1 text-[11.5px] font-semibold text-fg-muted hover:text-fg-accent hover:underline [&_svg]:size-3"
        title={context.page ? "The page’s text was shared with the question" : "Asked on this page"}
      >
        <FileText className="shrink-0" />
        <span className="truncate">
          {context.page ? "Shared " : "On "}
          {context.title || context.url}
        </span>
      </Link>
      {context.selection && (
        <blockquote className="m-0 max-w-[85%] border-r-2 border-gold-border pr-2.5 text-right font-serif text-[13px] italic leading-snug text-fg-secondary">
          “{quotePreview(context.selection, 220)}”
        </blockquote>
      )}
    </div>
  );
}

function Question({ text, context }: { text: string; context: SharedContext | null }) {
  return (
    <div className="flex flex-col gap-1.5">
      <SharedFrom context={context} />
      <div className="ml-8 self-end whitespace-pre-wrap rounded-md rounded-br-xs bg-surface-neutral px-3.5 py-2.5 text-[14px] leading-snug text-fg">
        {text}
      </div>
    </div>
  );
}

function SavedAnswer({ m }: { m: ChatMessage }) {
  return (
    <AnswerBody
      text={m.content === "(stopped)" ? "" : m.content}
      passages={m.passages ?? []}
      steps={savedSteps(m.steps)}
      notice={m.notice ?? null}
      error={m.error ?? null}
      stopped={m.stopped}
    />
  );
}

function AnswerBody({
  text,
  passages,
  steps,
  notice,
  error,
  streaming,
  stopped,
}: {
  text: string;
  passages: Passage[];
  steps: ToolStep[];
  notice: string | null;
  error: string | null;
  streaming?: boolean;
  stopped?: boolean;
}) {
  const byN = new Map(passages.map((p) => [p.n, p]));
  const blank = !text || text === NO_ANSWER;
  return (
    <div className="flex flex-col gap-2">
      <ToolSteps steps={steps} working={streaming && !text} />
      {notice && <p className="m-0 text-[12.5px] text-fg-muted">{notice}</p>}
      {error ? (
        <p
          role="alert"
          className="m-0 rounded-sm border border-red-border bg-red-surface px-3 py-2 text-[13px] text-fg-strong"
        >
          <b className="text-red-dark">No answer.</b> {error}
        </p>
      ) : blank ? (
        streaming &&
        !steps.length && (
          <p className="m-0 flex items-center gap-2 text-[13px] text-fg-secondary">
            <span aria-hidden className="size-2 animate-pulse rounded-full bg-blue" /> Thinking…
          </p>
        )
      ) : (
        <div className="font-serif text-[15px] leading-[1.55] text-fg [text-wrap:pretty]">
          <RichText
            text={text}
            renderCite={(n, key) => <CitationChip key={key} n={n} passage={byN.get(n)} compact />}
          />
          {streaming && (
            <span aria-hidden className="ml-1 inline-block h-4 w-1.5 translate-y-[2px] animate-pulse bg-blue" />
          )}
        </div>
      )}
      {stopped && <p className="m-0 text-[12.5px] text-fg-muted">Stopped.</p>}
    </div>
  );
}
