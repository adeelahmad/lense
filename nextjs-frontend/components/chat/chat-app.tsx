"use client";

import { useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, LibraryBig, Plus } from "lucide-react";
import { useSession } from "next-auth/react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Chats } from "@/app/openapi-client";
import type { AnswerCheck, Approval, ChatMessage, Estimate, Passage } from "@/app/openapi-client/types.gen";
import { Answer } from "@/components/chat/answer";
import { shortTitle } from "@/components/chat/cite";
import { Composer, ScopeBar } from "@/components/chat/composer";
import { ConversationList } from "@/components/chat/conversations";
import { useApprovals, useChat, useChats, useLlmStatus, useStopAnswer } from "@/components/chat/data";
import { EmptyChat } from "@/components/chat/empty";
import { fromApiScope, scopeFromParams, toApiScope, type Scope } from "@/components/chat/scope";
import { CitationSheet, SourcesPanel, SourcesSheet } from "@/components/chat/sources";
import { applyEvent, newTurn, savedSteps, type ToolStep, type TurnState } from "@/components/chat/stream";
import { useRecordingIndex, useSpeakerDirectory } from "@/components/search/data";
import { Banner } from "@/components/ui/banner";
import { Button, IconButton } from "@/components/ui/button";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { SSEError, streamSSE } from "@/lib/api/sse";
import { useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

type Live = { chatId: number; turn: TurnState };
type Extra = { steps: ToolStep[]; notice: string | null; error: string | null };
type Pair = { key: string; q: ChatMessage | null; a: ChatMessage | null };

function pairs(msgs: ChatMessage[]): Pair[] {
  const out: Pair[] = [];
  for (let i = 0; i < msgs.length; i++) {
    const m = msgs[i];
    if (m.role === "user") {
      const next = msgs[i + 1];
      if (next?.role === "assistant") {
        out.push({ key: `m${m.id}`, q: m, a: next });
        i++;
      } else out.push({ key: `m${m.id}`, q: m, a: null });
    } else out.push({ key: `m${m.id}`, q: null, a: m });
  }
  return out;
}

function UserBubble({ text }: { text: string }) {
  return (
    <div className="max-w-[520px] self-end whitespace-pre-wrap rounded-[16px_16px_4px_16px] bg-surface-neutral px-4 py-3 text-[15px] leading-normal text-fg">
      {text}
    </div>
  );
}

function streamError(e: unknown): string {
  if (e instanceof SSEError) {
    if (e.status === 401) return "You were signed out. Sign in again and retry";
    if (e.status === 404) return "This conversation isn’t available any more";
    if (e.status === 400) return "The question couldn’t be sent";
    return `The server answered ${e.status}`;
  }
  return e instanceof Error ? e.message : "The connection dropped";
}

/**
 * CH1–CH3, AI1–AI2: conversations with the archive. Lives in the chat layout so an answer keeps streaming while the
 * address changes from /chat to /chat/{id}.
 */
export function ChatApp() {
  const params = useParams<{ id?: string }>();
  const search = useSearchParams();
  const router = useRouter();
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const { data: session } = useSession();
  const { namespace: topNs, admin } = useArchive();
  const activeId = params?.id && /^\d+$/.test(params.id) ? Number(params.id) : null;

  const chats = useChats();
  const chat = useChat(activeId);
  const approvals = useApprovals(activeId);
  const llm = useLlmStatus();
  const index = useRecordingIndex();
  const dir = useSpeakerDirectory();

  const linkScope = useMemo(() => scopeFromParams(new URLSearchParams(search.toString())), [search]);
  const [draftScope, setDraftScope] = useState<Scope>(() => linkScope);
  const [draftModel, setDraftModel] = useState<string | null>(null); // for the conversation that isn't started yet
  const [draft, setDraft] = useState(() => search.get("q") ?? "");
  const [composing, setComposing] = useState(() =>
    Boolean(search.get("q") || search.get("ns") || search.get("recording") || search.get("speaker")),
  );
  const [live, setLive] = useState<Live | null>(null);
  const [extras, setExtras] = useState<Record<number, Extra>>({});
  const [partials, setPartials] = useState<Record<string, TurnState>>({});
  const [checks, setChecks] = useState<Record<number, AnswerCheck>>({});
  const [hover, setHover] = useState<number | null>(null);
  const [focusKey, setFocusKey] = useState<string | null>(null);
  const [preview, setPreview] = useState<Passage | null>(null);
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const [scopeOpenTick, setScopeOpenTick] = useState(0);
  const [announce, setAnnounce] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  const threadRef = useRef<HTMLDivElement>(null);

  // A new conversation starts from the top-bar namespace unless a link says otherwise.
  useEffect(() => {
    if (activeId == null && !search.toString()) setDraftScope(topNs ? { namespaces: [topNs] } : {});
  }, [topNs, activeId, search]);
  useEffect(() => {
    if (search.get("q")) setDraft(search.get("q") ?? "");
    if (search.toString()) setDraftScope(linkScope);
  }, [search, linkScope]);
  // A collection link (?collection=5) starts a conversation that draws on the collection (the link's scope has it).
  const collectionId = Number(search.get("collection")) || null;
  useEffect(() => {
    if (collectionId) setComposing(true);
  }, [collectionId]);

  const scope = activeId != null ? fromApiScope(chat.data?.scope) : draftScope;
  const streaming = live?.turn.status === "streaming";

  const setScope = useCallback(
    async (s: Scope) => {
      if (activeId == null) return setDraftScope(s);
      try {
        await data(
          Chats.updateChat({
            client,
            path: { cid: activeId },
            body: { scope: toApiScope(s) },
          }),
        );
        qc.invalidateQueries({ queryKey: ["chat", activeId] });
        qc.invalidateQueries({ queryKey: ["chats"] });
      } catch (e) {
        toast({
          title: "Couldn’t change the scope",
          body: e instanceof Error ? e.message : undefined,
          tone: "red",
        });
      }
    },
    [activeId, client, qc, toast],
  );

  const run = useCallback(
    async (cid: number, question: string, model?: string) => {
      const ac = new AbortController();
      abortRef.current = ac;
      let turn = newTurn(question);
      setLive({ chatId: cid, turn });
      setFocusKey("live");
      try {
        for await (const msg of streamSSE(`/api/v1/chats/${cid}/messages`, {
          method: "POST",
          body: { content: question, model },
          accessToken: session?.accessToken,
          signal: ac.signal,
        })) {
          turn = applyEvent(turn, msg);
          setLive({ chatId: cid, turn });
        }
        if (turn.status === "streaming")
          turn = {
            ...turn,
            status: turn.messageId != null ? "done" : "error",
            error: turn.messageId != null ? null : "The answer stopped before it finished",
          };
      } catch (e) {
        turn = ac.signal.aborted ? { ...turn, status: "stopped" } : { ...turn, status: "error", error: streamError(e) };
      }
      setLive({ chatId: cid, turn });
      const mid = turn.messageId;
      if (mid != null)
        setExtras((x) => ({
          ...x,
          [mid]: { steps: turn.steps, notice: turn.notice, error: turn.error },
        }));
      if (turn.status === "done") setAnnounce(`Answer: ${turn.text.replace(/\[\d+(?:,\s*\d+)*\]/g, "")}`);
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["chat", cid] }),
        qc.invalidateQueries({ queryKey: ["approvals", cid] }),
      ]);
      qc.invalidateQueries({ queryKey: ["chats"] });
      if (mid == null) setPartials((p) => ({ ...p, [`${cid}:${question}`]: turn }));
      setLive(null);
      setFocusKey(mid != null ? `a${mid}` : null);
    },
    [qc, session?.accessToken],
  );

  const send = useCallback(
    async (text?: string, model?: string) => {
      const question = (text ?? draft).trim();
      if (!question || streaming) return;
      setDraft("");
      let cid = activeId;
      if (cid == null) {
        try {
          const created = await data(
            Chats.createChat({
              client,
              body: { scope: toApiScope(draftScope), model: draftModel ?? undefined },
            }),
          );
          cid = created.id;
          qc.invalidateQueries({ queryKey: ["chats"] });
          setComposing(false);
          router.replace(`/chat/${cid}`);
        } catch (e) {
          setDraft(question);
          toast({
            title: "Couldn’t start a conversation",
            body: e instanceof Error ? e.message : undefined,
            tone: "red",
          });
          return;
        }
      }
      void run(cid, question, model);
    },
    [activeId, client, draft, draftScope, draftModel, qc, router, run, streaming, toast],
  );

  const stopAnswer = useStopAnswer();
  const [stopping, setStopping] = useState<number | null>(null); // the conversation whose answer is stopping
  const stop = () => {
    setStopping(live?.chatId ?? null);
    stopAnswer(live?.chatId, abortRef.current);
  };
  useEffect(() => {
    if (!live) setStopping(null);
  }, [live]);

  const newConversation = () => {
    abortRef.current?.abort();
    setDraft("");
    setComposing(true);
    setDraftScope(topNs ? { namespaces: [topNs] } : {});
    router.push("/chat");
  };

  // Keep the newest words in view while an answer streams, unless you scrolled up to read.
  useEffect(() => {
    const el = threadRef.current;
    if (!el || !live) return;
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 160) el.scrollTop = el.scrollHeight;
  }, [live]);
  useEffect(() => {
    const el = threadRef.current;
    if (el && chat.data) el.scrollTop = el.scrollHeight;
  }, [chat.data?.id, chat.data]);

  const msgs = chat.data?.messages ?? [];
  const items = useMemo(() => {
    const out = pairs(msgs);
    if (live && live.chatId === activeId) {
      const last = out[out.length - 1];
      if (last && !last.a && last.q?.content.trim() === live.turn.question.trim()) out.pop();
    }
    return out;
  }, [msgs, live, activeId]);
  // A new conversation's first answer shows at once, before the address has moved to /chat/{id}.
  const liveHere =
    live && (live.chatId === activeId || activeId == null) && !msgs.some((m) => m.id === live.turn.messageId)
      ? live.turn
      : null;

  // Approvals saved on the server sit before the first answer written after them.
  const liveApprovalIds = new Set(liveHere?.approvals.map((a) => a.id) ?? []);
  const approvalsFor = useMemo(() => {
    const map = new Map<number, Approval[]>();
    const answers = msgs.filter((m) => m.role === "assistant");
    for (const a of approvals.data ?? []) {
      const at = a.created_at ?? "";
      const target = answers.find((m) => (m.created_at ?? "") >= at) ?? answers[answers.length - 1];
      if (!target) continue;
      map.set(target.id, [...(map.get(target.id) ?? []), a]);
    }
    return map;
  }, [approvals.data, msgs]);

  const lastAnswer = [...msgs].reverse().find((m) => m.role === "assistant");
  const focus =
    focusKey === "live" && liveHere
      ? "live"
      : (focusKey ?? (liveHere ? "live" : lastAnswer ? `a${lastAnswer.id}` : null));
  const focusPassages: Passage[] | null =
    focus === "live"
      ? (liveHere?.passages ?? null)
      : focus
        ? (msgs.find((m) => `a${m.id}` === focus)?.passages ?? null)
        : null;

  const suggestions = useMemo(() => {
    const out = ["What topics came up most this month?"];
    const inScope = (ns: string | null | undefined) =>
      !scope.namespaces?.length || (ns != null && scope.namespaces.includes(ns));
    const top = [...dir.speakers]
      .filter((s) => inScope(s.namespace))
      .sort((a, b) => (b.talk_ms ?? 0) - (a.talk_ms ?? 0))[0];
    if (top) out.push(`Where did ${top.display} disagree with someone?`);
    const latest = (index.data ?? []).filter((r) => inScope(r.namespace))[0];
    if (latest) out.push(`Summarise ${shortTitle(latest.title, 40)} in five bullets`);
    return out;
  }, [dir.speakers, index.data, scope.namespaces]);

  // the model that answers here: the conversation's choice (or the one picked before it started), else the configured one
  const chosen = activeId != null ? (chat.data?.model ?? null) : draftModel;
  const model = llm.known && llm.configured ? (chosen ?? llm.model) : null;
  const pickModel = async (m: string) => {
    const next = m === llm.model ? null : m;
    if (activeId == null) return setDraftModel(next);
    try {
      await data(Chats.updateChat({ client, path: { cid: activeId }, body: { model: next } }));
      await qc.invalidateQueries({ queryKey: ["chat", activeId] });
      void qc.invalidateQueries({ queryKey: ["chats"] });
    } catch (e) {
      toast({ title: "Couldn’t change the model", body: e instanceof Error ? e.message : undefined, tone: "red" });
    }
  };
  const noProvider = llm.known && !llm.configured;
  const showThread = activeId != null || liveHere != null;
  const title = chat.data?.title ?? (activeId == null ? "New conversation" : "Conversation");
  const addScope = (ns: string) =>
    setScope({
      ...scope,
      namespaces: [...new Set([...(scope.namespaces ?? []), ns])],
    });
  const answerHandlers = (key: string) => ({
    hover: focus === key ? hover : null,
    onHover: (n: number | null) => {
      setHover(n);
      if (n != null) setFocusKey(key);
    },
    onPreview: setPreview,
    onShowSources: () => {
      setFocusKey(key);
      setSourcesOpen(true);
    },
  });

  const listPane = (
    <ConversationList
      chats={chats.data}
      loading={chats.isLoading}
      error={chats.isError ? chats.error.message : null}
      activeId={activeId}
      onNew={newConversation}
    />
  );

  return (
    <div className="grid h-[calc(100dvh-4rem)] grid-cols-1 md:grid-cols-[260px_minmax(0,1fr)] xl:grid-cols-[260px_minmax(0,1fr)_360px]">
      <h1 className="sr-only">Chat</h1>
      <div aria-live="polite" className="sr-only">
        {announce}
      </div>
      <aside
        className={cn(
          "min-h-0 overflow-y-auto border-r border-border bg-surface px-2.5 py-3.5",
          showThread || composing ? "hidden md:block" : "block",
        )}
      >
        {listPane}
      </aside>

      <section
        aria-label={title}
        className={cn("min-h-0 min-w-0 flex-col", showThread || composing ? "flex" : "hidden md:flex")}
      >
        <header className="flex h-12 shrink-0 items-center gap-2 border-b border-border px-2 md:hidden">
          <IconButton label="All conversations" onClick={() => (setComposing(false), router.push("/chat"))}>
            <ChevronLeft />
          </IconButton>
          <span className="min-w-0 flex-1 truncate text-[15.5px] font-bold text-fg">{title}</span>
          <IconButton label="Sources" onClick={() => setSourcesOpen(true)} disabled={!focusPassages?.length}>
            <LibraryBig />
          </IconButton>
        </header>

        <div ref={threadRef} className="min-h-0 flex-1 overflow-y-auto px-4 py-6 md:px-10">
          <div className="mx-auto flex max-w-[820px] flex-col gap-5">
            {activeId != null && chat.isLoading && (
              <div className="flex flex-col gap-4" aria-busy="true" aria-label="Loading conversation">
                <Skeleton className="ml-auto h-10 w-[45%] rounded-lg" />
                <Skeleton className="w-[80%]" />
                <Skeleton className="w-[70%]" />
              </div>
            )}
            {activeId != null && chat.isError && (
              <EmptyState
                tone="error"
                title={
                  chat.error.message === "not found"
                    ? "This conversation isn’t here"
                    : "Couldn’t open this conversation"
                }
                actions={
                  <Button variant="secondary" icon={<Plus />} onClick={newConversation}>
                    New conversation
                  </Button>
                }
              >
                {chat.error.message === "not found"
                  ? "It may have been deleted. Conversations are private to whoever started them."
                  : chat.error.message}
              </EmptyState>
            )}
            {!showThread && (
              <EmptyChat
                scope={scope}
                suggestions={suggestions}
                onAsk={(q) => send(q)}
                onChangeScope={() => setScopeOpenTick((t) => t + 1)}
                noProvider={noProvider}
                admin={admin}
              />
            )}
            {items.map((it) => {
              const aKey = it.a ? `a${it.a.id}` : it.key;
              const partial = !it.a && it.q ? partials[`${activeId}:${it.q.content}`] : undefined;
              const ex = it.a ? extras[it.a.id] : undefined;
              return (
                <div key={it.key} className="flex flex-col gap-5">
                  {it.q && <UserBubble text={it.q.content} />}
                  {it.a ? (
                    <Answer
                      chatId={activeId}
                      messageId={it.a.id}
                      question={it.q?.content ?? ""}
                      text={it.a.stopped && it.a.content === "(stopped)" ? "" : it.a.content}
                      passages={it.a.passages ?? []}
                      status={(ex?.error ?? it.a.error) ? "error" : it.a.stopped ? "stopped" : "done"}
                      error={ex?.error ?? it.a.error}
                      notice={ex?.notice ?? it.a.notice}
                      steps={ex?.steps ?? savedSteps(it.a.steps)}
                      approvals={(approvalsFor.get(it.a.id) ?? [])
                        .filter((a) => !liveApprovalIds.has(a.id))
                        .map((a) => ({
                          ...a,
                          estimate: a.estimate as Estimate | null,
                        }))}
                      scope={scope}
                      model={it.a.model ?? model}
                      onRetry={it.q ? () => send(it.q!.content) : undefined}
                      models={llm.models}
                      onRetryWith={it.q ? (m) => send(it.q!.content, m) : undefined}
                      onAddScope={addScope}
                      check={checks[it.a.id] ?? it.a.check}
                      onChecked={(c) => setChecks((x) => ({ ...x, [it.a!.id]: c }))}
                      {...answerHandlers(aKey)}
                    />
                  ) : partial ? (
                    <Answer
                      chatId={activeId}
                      messageId={null}
                      question={partial.question}
                      text={partial.text}
                      passages={partial.passages}
                      status={partial.status}
                      error={partial.error}
                      steps={partial.steps}
                      scope={scope}
                      model={model}
                      onRetry={() => send(partial.question)}
                      models={llm.models}
                      onRetryWith={(m) => send(partial.question, m)}
                      {...answerHandlers(aKey)}
                    />
                  ) : (
                    it.q &&
                    !liveHere && (
                      <p className="m-0 text-[13px] text-fg-muted">
                        No answer was saved for this question. Ask it again to get one.
                      </p>
                    )
                  )}
                </div>
              );
            })}
            {liveHere && (
              <div className="flex flex-col gap-5">
                <UserBubble text={liveHere.question} />
                <Answer
                  chatId={activeId}
                  messageId={liveHere.messageId}
                  question={liveHere.question}
                  text={liveHere.text}
                  passages={liveHere.passages}
                  status={liveHere.status}
                  error={liveHere.error}
                  notice={liveHere.notice}
                  steps={liveHere.steps}
                  approvals={liveHere.approvals}
                  scope={scope}
                  model={model}
                  onStop={stop}
                  stopping={stopping != null && stopping === live?.chatId}
                  onRetry={() => send(liveHere.question)}
                  onAddScope={addScope}
                  {...answerHandlers("live")}
                />
              </div>
            )}
          </div>
        </div>

        <div className="shrink-0 border-t border-border px-4 pb-4 pt-3 md:px-10">
          <div className="mx-auto flex max-w-[820px] flex-col gap-2.5">
            {noProvider && showThread && (
              <Banner tone="info">
                No LLM provider is set up, so answers list the best passages.{" "}
                {admin && (
                  <Link href="/settings#llm" className="font-bold text-fg-accent hover:underline">
                    Open LLM settings
                  </Link>
                )}
              </Banner>
            )}
            <ScopeBarWithOpen
              scope={scope}
              onChange={setScope}
              model={model}
              tools={llm.known ? llm.tools : null}
              models={llm.models}
              onModel={(m) => void pickModel(m)}
              openTick={scopeOpenTick}
            />
            <Composer
              value={draft}
              onChange={setDraft}
              onSend={() => send()}
              busy={streaming}
              autoFocus={composing}
              placeholder={msgs.length || liveHere ? "Ask a follow-up…" : "Ask across your archive…"}
            />
          </div>
        </div>
      </section>

      <aside
        aria-label="Sources"
        className="hidden min-h-0 overflow-y-auto border-l border-border px-[18px] py-4 xl:block"
      >
        <SourcesPanel
          passages={focusPassages}
          hover={hover}
          onHover={setHover}
          loading={Boolean(liveHere && !liveHere.passages)}
        />
      </aside>

      <CitationSheet passage={preview} onClose={() => setPreview(null)} />
      <SourcesSheet open={sourcesOpen} onOpenChange={setSourcesOpen} passages={focusPassages} />
    </div>
  );
}

/** The scope bar, opened from outside (the empty state's "Change scope"). */
function ScopeBarWithOpen({ openTick, ...props }: Parameters<typeof ScopeBar>[0] & { openTick: number }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (openTick) ref.current?.querySelector<HTMLButtonElement>("button[aria-haspopup='dialog']")?.click();
  }, [openTick]);
  return (
    <div ref={ref}>
      <ScopeBar {...props} />
    </div>
  );
}
