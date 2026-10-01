"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUp, MessagesSquare, Plus, Square } from "lucide-react";
import Link from "next/link";
import { useSession } from "next-auth/react";
import { useEffect, useRef, useState } from "react";

import { Chats } from "@/app/openapi-client";
import { useStopAnswer } from "@/components/chat/data";
import { usePlayerApi } from "@/components/player/media";
import {
  applyChatEvent,
  citeParts,
  isRecordingChat,
  newAnswer,
  type Answer,
  type Passage,
} from "@/components/recording/chat-model";
import { useRec } from "@/components/recording/context";
import { Button } from "@/components/ui/button";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { SSEError, streamSSE } from "@/lib/api/sse";
import { tc } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * Chat scoped to this recording: questions are answered from its transcript only, and each citation seeks the player.
 * The conversation is kept (it's this recording's chat in Chat too); the full chat UI lives on the Chat page.
 */
/** The recording's chat, for people with a role in its namespace (the assistant answers from the namespace). */
export function ChatTab() {
  const { member, ns } = useRec();
  if (!member)
    return (
      <EmptyState icon={<MessagesSquare />} title={`Chat needs a role in ${ns ?? "the namespace"}`} className="py-10">
        You see this recording through a role on its collection. Asking the assistant about recordings needs a role in
        the namespace: ask an owner of {ns ?? "it"}.
      </EmptyState>
    );
  return <ChatPanel />;
}

function ChatPanel() {
  const { id, model, chatDraft, clearChatDraft } = useRec();
  const client = useApiClient();
  const qc = useQueryClient();
  const { data: session } = useSession();
  const [cid, setCid] = useState<number | null>(null);
  const [fresh, setFresh] = useState(false);
  const [input, setInput] = useState("");
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [stopping, setStopping] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const stopAnswer = useStopAnswer();
  const box = useRef<HTMLTextAreaElement>(null);
  const end = useRef<HTMLDivElement>(null);

  const chats = useQuery({
    queryKey: ["chats"],
    queryFn: () => data(Chats.listChats({ client })),
    staleTime: 30_000,
  });
  const existing = fresh ? null : (chats.data ?? []).find((c) => isRecordingChat(c.scope, id));
  const chatId = cid ?? existing?.id ?? null;
  const chat = useQuery({
    queryKey: ["chat", chatId],
    queryFn: () => data(Chats.getChat({ client, path: { cid: chatId as number } })),
    enabled: chatId != null,
  });

  useEffect(() => {
    if (chatDraft == null) return;
    setInput((v) => (v.trim() ? `${chatDraft}${v}` : chatDraft));
    clearChatDraft();
    requestAnimationFrame(() => {
      box.current?.focus();
      const n = box.current?.value.length ?? 0;
      box.current?.setSelectionRange(n, n);
    });
  }, [chatDraft, clearChatDraft]);
  useEffect(() => end.current?.scrollIntoView({ block: "end" }), [answer?.text, chat.data?.messages?.length]);
  useEffect(() => () => abort.current?.abort(), []);

  const send = async () => {
    const q = input.trim();
    if (!q || answer?.status === "streaming") return;
    setInput("");
    setAnswer(newAnswer(q));
    setStopping(false);
    const ctl = new AbortController();
    abort.current = ctl;
    try {
      let target = chatId;
      if (target == null) {
        const made = await data(
          Chats.createChat({
            client,
            body: {
              title: `About “${model.title}”`.slice(0, 120),
              scope: { recordings: [id] },
            },
          }),
        );
        target = made.id;
        setCid(made.id);
        setFresh(false);
        void qc.invalidateQueries({ queryKey: ["chats"] });
      }
      for await (const ev of streamSSE(`/api/v1/chats/${target}/messages`, {
        method: "POST",
        body: { content: q },
        accessToken: session?.accessToken,
        signal: ctl.signal,
      })) {
        setAnswer((a) => (a ? applyChatEvent(a, ev) : a));
      }
      setAnswer((a) => (a && a.status === "streaming" ? { ...a, status: "done" } : a));
      await qc.invalidateQueries({ queryKey: ["chat", target] });
      setAnswer((a) => (a?.status === "done" || a?.saved ? null : a)); // a saved one shows in the conversation now
    } catch (e) {
      if (ctl.signal.aborted) setAnswer((a) => (a ? { ...a, status: "stopped" } : a));
      else {
        const msg =
          e instanceof ApiError
            ? e.message
            : e instanceof SSEError
              ? e.status === 403
                ? "This account can't start conversations (read-only token)."
                : "The chat service didn't answer."
              : "The chat service didn't answer.";
        setAnswer((a) => (a ? { ...a, status: "error", error: msg } : a));
      }
    }
  };

  const messages = chat.data?.messages ?? [];
  const streaming = answer?.status === "streaming";
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2 border-b border-border px-5 py-2.5">
        <span className="inline-flex h-6 items-center gap-1.5 rounded-pill border border-blue-border bg-blue-surface px-2.5 text-[12px] font-semibold text-blue-dark">
          <MessagesSquare className="size-3.5" /> This recording
        </span>
        <span className="min-w-0 flex-1 truncate text-[12px] text-fg-muted">
          Answers come only from its transcript and cite the moment.
        </span>
        {chatId != null && (
          <Button
            variant="ghost"
            size="xs"
            icon={<Plus />}
            disabled={streaming}
            onClick={() => {
              setCid(null);
              setFresh(true);
              setAnswer(null);
            }}
          >
            New
          </Button>
        )}
      </div>
      <div
        className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-5 py-4"
        aria-live="polite"
        aria-busy={streaming}
      >
        {chat.isLoading && chatId != null && <Skeleton className="w-3/4" />}
        {!messages.length && !answer && !chat.isLoading && (
          <div className="flex flex-col gap-2 py-6 text-center">
            <p className="text-[14px] font-bold text-fg">Ask about this recording</p>
            <p className="text-[13px] leading-normal text-fg-secondary">
              For example: “What did they decide?”, “Who raised the budget?”. Select text in the transcript and choose
              Ask in chat to quote it.
            </p>
          </div>
        )}
        {messages.map((m) =>
          m.role === "user" ? (
            <Question key={m.id} text={m.content} />
          ) : (
            <div key={m.id} className="flex flex-col gap-1.5">
              {m.notice && <p className="text-[12.5px] text-fg-muted">{m.notice}</p>}
              {(m.steps ?? []).length > 0 && (
                <p className="text-[12.5px] text-fg-muted">
                  {(m.steps ?? []).map((x) => x.summary || x.tool).join(" · ")}
                </p>
              )}
              {m.error ? (
                <p className="rounded-sm border border-red-border bg-red-surface px-3 py-2 text-[13px] text-fg-strong">
                  <b className="text-red-dark">No answer.</b> {m.error}
                </p>
              ) : (
                <AnswerView
                  text={m.content === "(stopped)" ? "" : m.content}
                  passages={(m.passages ?? []) as Passage[]}
                />
              )}
              {m.stopped && <p className="text-[12.5px] text-fg-muted">Stopped.</p>}
            </div>
          ),
        )}
        {answer && (
          <>
            <Question text={answer.question} />
            {answer.notice && <p className="text-[12.5px] text-fg-muted">{answer.notice}</p>}
            {answer.steps.length > 0 && <p className="text-[12.5px] text-fg-muted">{answer.steps.join(" · ")}</p>}
            {answer.text ? (
              <AnswerView text={answer.text} passages={answer.passages} streaming={streaming} />
            ) : (
              streaming && (
                <p className="flex items-center gap-2 text-[13px] text-fg-secondary">
                  <span aria-hidden className="size-2 animate-pulse rounded-full bg-blue" /> Reading the transcript…
                </p>
              )
            )}
            {answer.status === "error" && (
              <p
                role="alert"
                className="rounded-sm border border-red-border bg-red-surface px-3 py-2 text-[13px] text-fg-strong"
              >
                <b className="text-red-dark">No answer.</b> {answer.error} An admin can check the AI provider in
                Settings.
              </p>
            )}
            {answer.status === "stopped" && <p className="text-[12.5px] text-fg-muted">Stopped.</p>}
          </>
        )}
        <div ref={end} />
      </div>
      <form
        className="flex items-end gap-2 border-t border-border px-4 py-3"
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
      >
        <textarea
          ref={box}
          value={input}
          rows={Math.min(6, Math.max(1, input.split("\n").length))}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void send();
            }
          }}
          placeholder="Ask about this recording…"
          aria-label="Ask about this recording"
          className="max-h-40 min-h-10 flex-1 resize-none rounded-sm border border-border bg-background px-3 py-2 text-[14px] leading-snug text-fg outline-none placeholder:text-fg-muted focus:border-blue focus:shadow-[0_0_0_3px_var(--intent-surface)]"
        />
        {streaming ? (
          <Button
            variant="secondary"
            size="md"
            icon={<Square />}
            onClick={() => {
              setStopping(true);
              stopAnswer(chatId, abort.current);
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
      </form>
    </div>
  );
}

function Question({ text }: { text: string }) {
  return (
    <div className="ml-8 self-end whitespace-pre-wrap rounded-md rounded-br-xs bg-surface-neutral px-3.5 py-2.5 text-[14px] leading-snug text-fg">
      {text}
    </div>
  );
}

function AnswerView({ text, passages, streaming }: { text: string; passages: Passage[]; streaming?: boolean }) {
  const { id } = useRec();
  const api = usePlayerApi();
  const byN = new Map(passages.map((p) => [p.n, p]));
  return (
    <div
      className={cn(
        "whitespace-pre-wrap font-serif text-[15.5px] leading-[1.55] text-fg",
        streaming &&
          "after:ml-0.5 after:inline-block after:h-4 after:w-1.5 after:animate-pulse after:bg-blue after:align-middle",
      )}
    >
      {citeParts(text).map((p, i) => {
        if (p.kind === "text") return <span key={i}>{p.text}</span>;
        const ps = byN.get(p.n);
        if (!ps)
          return (
            <sup key={i} className="text-fg-muted">
              [{p.n}]
            </sup>
          );
        const label = `${tc(ps.t0 ?? 0)}${ps.speaker ? ` · ${ps.speaker}` : ""}`;
        const cls =
          "mx-0.5 inline-flex h-[20px] items-center rounded-pill border border-blue-border bg-blue-surface px-1.5 align-[2px] font-sans text-[11.5px] font-semibold text-blue-dark hover:bg-blue hover:text-white";
        return ps.recording_id === id ? (
          <button
            key={i}
            type="button"
            className={cls}
            title={ps.text}
            onClick={() => api.seek(ps.t0 ?? 0, { manual: true })}
            aria-label={`Go to ${label}`}
          >
            {label}
          </button>
        ) : (
          <Link
            key={i}
            href={`/recordings/${ps.recording_id}?t=${Math.floor((ps.t0 ?? 0) / 1000)}`}
            className={cls}
            title={ps.text}
          >
            {ps.title ?? "Recording"} · {label}
          </Link>
        );
      })}
    </div>
  );
}
