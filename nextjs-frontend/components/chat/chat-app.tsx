"use client";

import { useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, Clock, FolderKanban, LibraryBig, Loader2, Plus, X } from "lucide-react";
import { useSession } from "next-auth/react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Chats, Namespaces } from "@/app/openapi-client";
import type { AnswerCheck, Approval, Chat, ChatMessage, Estimate, Passage } from "@/app/openapi-client/types.gen";
import { Answer } from "@/components/chat/answer";
import { AttachmentChips, useAttachments } from "@/components/chat/attachments";
import { shortTitle } from "@/components/chat/cite";
import { Composer, ScopeBar } from "@/components/chat/composer";
import { ConversationList } from "@/components/chat/conversations";
import { refetchFresh, useApprovals, useChat, useChats, useLlmStatus, useStopAnswer } from "@/components/chat/data";
import { EmptyChat } from "@/components/chat/empty";
import { ExportMenu, useChatExport } from "@/components/chat/export";
import { NamespaceOffer, spokenPick } from "@/components/chat/namespace-offer";
import { ReportActions, ResearchCard } from "@/components/chat/research";
import { fromApiScope, scopeFromParams, toApiScope, type Scope } from "@/components/chat/scope";
import { CitationSheet, SourcesPanel, SourcesSheet } from "@/components/chat/sources";
import { ToolsChip, useSaveTools, useToolCatalog, type ToolChoice } from "@/components/chat/tools-picker";
import {
  applyEvent,
  newTurn,
  savedSteps,
  type Suggested,
  type ToolStep,
  type TurnState,
} from "@/components/chat/stream";
import { onlyFiles, UserBubble } from "@/components/chat/user-bubble";
import { SummaryMark } from "@/components/chat/summary-mark";
import { switched, VersionSwitch } from "@/components/chat/versions";
import { SharedFrom } from "@/components/page-chat/page-chat";
import { useRecordingIndex, useSpeakerDirectory } from "@/components/search/data";
import { Banner } from "@/components/ui/banner";
import { Button, IconButton } from "@/components/ui/button";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { SSEError, streamSSE } from "@/lib/api/sse";
import { useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";
import { useVoice, VoiceError } from "@/lib/voice";

type Sent = { id: string; filename: string; size: number };
/** An answer being written; `beside`: the message it makes a version of (an edited question, a regenerated answer). */
type Live = { chatId: number; turn: TurnState; files: Sent[]; beside?: number };
type Queued = { key: number; text: string; files: Sent[]; research?: boolean };

/** /clear and /compact, which the model doesn't answer, and /research, answered with a plan to approve: there's nothing
 * to regenerate. */
const COMMAND = /^\s*\/(clear|compact|research)(\s|$)/i;

/** What a message with only files says (the server says the same when it gets none). */
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
  const approvals = useApprovals(activeId);
  const llm = useLlmStatus();
  const index = useRecordingIndex();
  const dir = useSpeakerDirectory();

  const linkScope = useMemo(() => scopeFromParams(new URLSearchParams(search.toString())), [search]);
  const [draftScope, setDraftScope] = useState<Scope>(() => linkScope);
  const [draftModel, setDraftModel] = useState<string | null>(null); // for the conversation that isn't started yet
  const [draftTools, setDraftTools] = useState<ToolChoice | null>(null); // and its tools, when picked before it starts
  const [draft, setDraft] = useState(() => search.get("q") ?? "");
  const [composing, setComposing] = useState(() =>
    Boolean(
      search.get("q") ||
      search.get("ns") ||
      search.get("recording") ||
      search.get("speaker") ||
      search.get("global") ||
      search.get("voice"),
    ),
  );
  const [live, setLive] = useState<Live | null>(null);
  // an answer written elsewhere (a project's brief, in the background) shows here once it's saved
  const chat = useChat(activeId, { poll: !live || live.chatId !== activeId });
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
  const spoken = useRef<"voice" | undefined>(undefined); // the question being sent was said, in voice mode
  const files = useAttachments();
  const [queue, setQueue] = useState<Queued[]>([]);
  const threadRef = useRef<HTMLDivElement>(null);

  // The browser prints the conversation, or one message and its question, when the server can't make its PDF.
  const [printing, setPrinting] = useState<{ message?: number } | null>(null);
  useEffect(() => {
    if (!printing) return;
    const done = () => setPrinting(null);
    window.addEventListener("afterprint", done, { once: true });
    window.print();
    return () => window.removeEventListener("afterprint", done);
  }, [printing]);
  const exporter = useChatExport(activeId, chat.data?.title, (message) => setPrinting({ message }));

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

  // A conversation over everything was narrowed to the namespace its question is about: say so, with a way back.
  const narrowed = useCallback(
    (cid: number, namespaces: string[]) => {
      void qc.invalidateQueries({ queryKey: ["chat", cid] });
      toast({
        title: `Looking in ${namespaces.join(", ")}`,
        body: "Picked from your question.",
        action: {
          label: "Use everything",
          onClick: async () => {
            try {
              await data(Chats.updateChat({ client, path: { cid }, body: { scope: {} } }));
              void qc.invalidateQueries({ queryKey: ["chat", cid] });
              void qc.invalidateQueries({ queryKey: ["chats"] });
            } catch (e) {
              toast({
                title: "Couldn’t change the scope",
                body: e instanceof Error ? e.message : undefined,
                tone: "red",
              });
            }
          },
        },
      });
    },
    [client, qc, toast],
  );

  // Namespaces offered when it isn't clear which one a conversation over everything is about; nothing changes until
  // one is tapped (or said, in voice mode).
  const [offer, setOffer] = useState<{ cid: number; items: Suggested[] } | null>(null);
  const offerRef = useRef(offer);
  offerRef.current = offer;
  const [placing, setPlacing] = useState(false);
  const pickNamespace = useCallback(
    async (cid: number, it: Suggested) => {
      setPlacing(true);
      try {
        if (it.new) {
          await data(Namespaces.createNamespace({ client, body: { name: it.name } }));
          void qc.invalidateQueries({ queryKey: ["namespaces"] });
          void qc.invalidateQueries({ queryKey: ["me"] });
        }
        await data(Chats.updateChat({ client, path: { cid }, body: { scope: { namespaces: [it.name] } } }));
        setOffer(null);
        void qc.invalidateQueries({ queryKey: ["chat", cid] });
        void qc.invalidateQueries({ queryKey: ["chats"] });
        toast({ title: it.new ? `Created ${it.name} and using it here` : `Using ${it.name} here`, tone: "green" });
        return true;
      } catch (e) {
        toast({
          title: it.new ? `Couldn’t create ${it.name}` : "Couldn’t change the scope",
          body: e instanceof Error ? e.message : undefined,
          tone: "red",
        });
        return false;
      } finally {
        setPlacing(false);
      }
    },
    [client, qc, toast],
  );

  const run = useCallback(
    async (
      cid: number,
      question: string,
      model?: string,
      attached: Sent[] = [],
      edit?: number,
      regenerate?: number,
      research?: boolean,
    ): Promise<TurnState> => {
      // a new question goes on from the version showing; an edit or a regenerate is a version beside its message
      const shown = qc.getQueryData<Chat>(["chat", cid])?.messages ?? [];
      const parent = edit == null && regenerate == null ? shown[shown.length - 1]?.id : undefined;
      const beside = edit ?? regenerate;
      const ac = new AbortController();
      abortRef.current = ac;
      let turn = newTurn(question);
      const show = (t: TurnState) => setLive({ chatId: cid, turn: t, files: attached, beside });
      show(turn);
      setFocusKey("live");
      try {
        for await (const msg of streamSSE(`/api/v1/chats/${cid}/messages`, {
          method: "POST",
          body:
            regenerate != null
              ? { regenerate, model }
              : {
                  content: question,
                  model,
                  attachments: attached.map((f) => f.id),
                  edit,
                  parent,
                  via: spoken.current,
                  research: research || undefined,
                },
          accessToken: session?.accessToken,
          signal: ac.signal,
        })) {
          const was = turn.scoped;
          turn = applyEvent(turn, msg);
          show(turn);
          if (turn.scoped && !was) narrowed(cid, turn.scoped);
          if (msg.event === "suggested" && turn.suggested?.length) setOffer({ cid, items: turn.suggested });
          // the plan's card reads the research from here on
          if (msg.event === "research" && turn.research) qc.setQueryData(["research", turn.research.id], turn.research);
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
      show(turn);
      const mid = turn.messageId;
      if (mid != null)
        setExtras((x) => ({
          ...x,
          [mid]: { steps: turn.steps, notice: turn.notice, error: turn.error },
        }));
      if (turn.status === "done") setAnnounce(`Answer: ${turn.text.replace(/\[\d+(?:,\s*\d+)*\]/g, "")}`);
      await Promise.all([refetchFresh(qc, ["chat", cid]), refetchFresh(qc, ["approvals", cid])]);
      qc.invalidateQueries({ queryKey: ["chats"] });
      if (mid == null) setPartials((p) => ({ ...p, [`${cid}:${question}`]: turn }));
      setLive(null);
      setFocusKey(mid != null ? `a${mid}` : null);
      return turn;
    },
    [qc, session?.accessToken, narrowed],
  );

  // Sends now, starting the conversation if there isn't one yet.
  const dispatch = useCallback(
    async (
      question: string,
      model?: string,
      attached: Sent[] = [],
      kind: "chat" | "setup" = "chat",
      edit?: number,
      research?: boolean,
    ): Promise<TurnState | null> => {
      let cid = kind === "setup" ? null : activeId;
      if (cid == null) {
        try {
          const created = await data(
            Chats.createChat({
              client,
              body:
                kind === "setup"
                  ? { kind }
                  : { scope: toApiScope(draftScope), model: draftModel ?? undefined, ...(draftTools ?? {}) },
            }),
          );
          cid = created.id;
          qc.invalidateQueries({ queryKey: ["chats"] });
          if (draftTools) {
            setDraftTools(null); // it's the latest choice now: the next new conversation starts with it
            void qc.invalidateQueries({ queryKey: ["chat-tools"] });
          }
          setComposing(false);
          router.replace(`/chat/${cid}`);
        } catch (e) {
          setDraft(question);
          toast({
            title: "Couldn’t start a conversation",
            body: e instanceof Error ? e.message : undefined,
            tone: "red",
          });
          return null;
        }
      }
      return run(cid, question, model, attached, edit, undefined, research);
    },
    [activeId, client, draftScope, draftModel, draftTools, qc, router, run, toast],
  );

  // The Deep research switch: what's typed next is a research goal, answered with a plan to approve.
  const [researchOn, setResearchOn] = useState(false);

  // What you send while an answer is being written waits its turn, so you never have to wait to type.
  const send = useCallback(
    (text?: string, model?: string) => {
      const attached: Sent[] =
        text == null ? files.take().map((x) => ({ id: x.upload!.id, filename: x.file.name, size: x.file.size })) : [];
      const question = (text ?? draft).trim() || (attached.length ? onlyFiles(attached.length) : "");
      if (!question) return;
      const research = text == null && researchOn && !attached.length;
      if (text == null) setDraft("");
      if (research) setResearchOn(false);
      if (live) {
        setQueue((q) => [...q, { key: Date.now() + q.length, text: question, files: attached, research }]);
        return;
      }
      void dispatch(question, model, attached, "chat", undefined, research);
    },
    [dispatch, draft, files, live, researchOn],
  );
  useEffect(() => {
    if (live || !queue.length) return;
    const [next, ...rest] = queue;
    setQueue(rest);
    void dispatch(next.text, undefined, next.files, "chat", undefined, next.research);
  }, [live, queue, dispatch]);

  // Regenerating writes a new answer to the same question, as a version beside the old one, streaming in its place.
  const regenerate = useCallback(
    (q: ChatMessage, a: ChatMessage, model?: string) => {
      if (live || activeId == null) return;
      const attached: Sent[] = (q.attachments ?? []).map(({ id, filename, size }) => ({ id, filename, size }));
      void run(activeId, q.content, model, attached, undefined, a.id);
    },
    [activeId, live, run],
  );

  // Every message seen (ids are unique across conversations), of any version, so switching back to a version already
  // seen shows it at once.
  const seen = useRef(new Map<number, ChatMessage>());
  useEffect(() => {
    for (const m of chat.data?.messages ?? []) seen.current.set(m.id, m);
  }, [chat.data]);
  const switchTo = useCallback(
    async (id: number) => {
      if (activeId == null || live) return;
      const key = ["chat", activeId];
      const was = qc.getQueryData<Chat>(key);
      const shown = was ? switched(was, id, seen.current) : null;
      if (shown) qc.setQueryData(key, shown);
      try {
        qc.setQueryData(
          key,
          await data(Chats.switchVersion({ client, path: { cid: activeId }, body: { message: id } })),
        );
      } catch (e) {
        if (was) qc.setQueryData(key, was);
        toast({ title: "Couldn’t show that version", body: e instanceof Error ? e.message : undefined, tone: "red" });
      }
    },
    [activeId, client, live, qc, toast],
  );

  // Editing a question asks it again (with the files it had), as a new version beside it.
  const editQuestion = useCallback(
    (q: ChatMessage, text: string) => {
      const attached: Sent[] = (q.attachments ?? []).map(({ id, filename, size }) => ({ id, filename, size }));
      const question = text.trim() || (attached.length ? onlyFiles(attached.length) : "");
      if (!question || live) return;
      void dispatch(question, undefined, attached, "chat", q.id);
    },
    [dispatch, live],
  );

  // Voice mode: hear a question, send it, read the answer aloud, listen again, until the mic is tapped off or
  // nothing is said twice in a row. /chat?voice=1 (the assistant home's mic) starts it at once.
  const voice = useVoice();
  const [voiceOn, setVoiceOn] = useState(() => search.get("voice") === "1");
  const voiceRef = useRef(voiceOn);
  const dispatchRef = useRef(dispatch);
  dispatchRef.current = dispatch;
  const looping = useRef(false);
  const { listen, speak, stop: hush } = voice;
  useEffect(() => {
    voiceRef.current = voiceOn;
    if (!voiceOn) {
      hush();
      return;
    }
    if (looping.current) return;
    looping.current = true;
    void (async () => {
      let quiet = 0;
      try {
        while (voiceRef.current) {
          let said: string;
          try {
            said = await listen();
          } catch (e) {
            if (voiceRef.current)
              toast({
                title: "Couldn’t listen",
                body: e instanceof VoiceError ? e.message : undefined,
                tone: "red",
              });
            break;
          }
          if (!voiceRef.current) break;
          if (!said) {
            if (++quiet >= 2) break;
            continue;
          }
          quiet = 0;
          // naming one of the offered namespaces picks it, rather than asking a question
          const o = offerRef.current;
          const named = o ? spokenPick(said, o.items) : null;
          if (o && named) {
            const ok = await pickNamespace(o.cid, named);
            if (voiceRef.current) await speak(ok ? `Okay, using ${named.name}.` : `I couldn't use ${named.name}.`);
            continue;
          }
          spoken.current = "voice"; // the question's record says it was said
          const turn = await dispatchRef.current(said).finally(() => (spoken.current = undefined));
          if (!voiceRef.current || !turn) break;
          if (turn.status === "done" && turn.text) await speak(turn.text);
        }
      } finally {
        looping.current = false;
        setVoiceOn(false);
      }
    })();
  }, [voiceOn, listen, speak, hush, toast, pickNamespace]);
  useEffect(
    () => () => {
      voiceRef.current = false;
    },
    [],
  );

  // /chat?setup=1 (from the setup wizard): a conversation in which the assistant sets the server up.
  const setupAsked = useRef(false);
  useEffect(() => {
    if (!admin || setupAsked.current || search.get("setup") !== "1") return;
    setupAsked.current = true;
    void dispatch("Help me set up Lens.", undefined, [], "setup");
  }, [admin, search, dispatch]);

  // /chat?q=...&send=1 (asked from Home): the question is sent as it arrives, not left in the composer.
  const linkAsked = useRef(false);
  useEffect(() => {
    const q = search.get("q")?.trim();
    if (linkAsked.current || search.get("send") !== "1" || !q) return;
    linkAsked.current = true;
    setDraft("");
    void dispatch(q);
  }, [search, dispatch]);

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
  // where the model stops reading the conversation word for word: under the last message its summary covers
  const memory = chat.data?.summary ?? null;
  const items = useMemo(() => {
    const here = live && live.chatId === activeId;
    // a version being written streams in place of the one beside it (what came before it has smaller ids)
    const beside = here ? live.beside : undefined;
    const out = pairs(beside != null ? msgs.filter((m) => m.id < beside) : msgs);
    if (here) {
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

  // Approvals saved on the server sit before the first answer written after them, in whichever version that is (so one
  // proposed in another version shows there).
  const liveApprovalIds = new Set(liveHere?.approvals.map((a) => a.id) ?? []);
  const tree = chat.data?.tree;
  const approvalsFor = useMemo(() => {
    const map = new Map<number, Approval[]>();
    const answers = (tree?.length ? tree : msgs).filter((m) => m.role === "assistant");
    const lastShown = msgs.filter((m) => m.role === "assistant").pop();
    for (const a of approvals.data ?? []) {
      const at = a.created_at ?? "";
      const target = answers.find((m) => (m.created_at ?? "") >= at) ?? lastShown;
      if (!target) continue;
      map.set(target.id, [...(map.get(target.id) ?? []), a]);
    }
    return map;
  }, [approvals.data, msgs, tree]);

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

  // what the answers may use: the conversation's choice, else the one picked before it started, else your latest
  const usesTools = llm.known && llm.configured && llm.tools;
  const toolCatalog = useToolCatalog(activeId, usesTools);
  const toolChoice: ToolChoice =
    activeId != null
      ? { tools: chat.data?.tools ?? null, max_tool_calls: chat.data?.max_tool_calls ?? null }
      : (draftTools ?? {
          tools: toolCatalog.data?.remembered.tools ?? null,
          max_tool_calls: toolCatalog.data?.remembered.max_tool_calls ?? null,
        });
  const saveTools = useSaveTools(activeId);
  const pickTools = (v: ToolChoice) => (activeId == null ? setDraftTools(v) : void saveTools(v));
  const showThread = activeId != null || liveHere != null;
  // setting the server up doesn't look in the archive: no sources column to fill
  const setupChat = activeId != null && chat.data?.kind === "setup";
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
    <div
      className={cn(
        "grid h-[calc(100dvh-4rem-var(--lens-banner,0px))] grid-cols-1 md:grid-cols-[260px_minmax(0,1fr)] print:block! print:h-auto",
        !setupChat && "xl:grid-cols-[260px_minmax(0,1fr)_360px]",
      )}
    >
      <h1 className="sr-only">Chat</h1>
      <div aria-live="polite" className="sr-only">
        {announce}
      </div>
      <aside
        className={cn(
          "min-h-0 overflow-y-auto border-r border-border bg-surface px-2.5 py-3.5 print:hidden!",
          showThread || composing ? "hidden md:block" : "block",
        )}
      >
        {listPane}
      </aside>

      <section
        aria-label={title}
        className={cn("min-h-0 min-w-0 flex-col", showThread || composing ? "flex" : "hidden md:flex")}
      >
        <header
          className={cn(
            "flex h-12 shrink-0 items-center gap-2 border-b border-border px-2 md:px-4 print:border-0 print:px-0",
            activeId == null && "md:hidden",
          )}
        >
          <IconButton
            label="All conversations"
            onClick={() => (setComposing(false), router.push("/chat"))}
            className="md:hidden print:hidden"
          >
            <ChevronLeft />
          </IconButton>
          <span className="min-w-0 flex-1 truncate text-[15.5px] font-bold text-fg">{title}</span>
          {chat.data?.project != null && (
            <Link
              href={`/projects/${chat.data.project}`}
              className="inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-[13px] text-fg-secondary hover:bg-surface-neutral print:hidden"
            >
              <FolderKanban aria-hidden className="size-3.5" />
              Project
            </Link>
          )}
          {activeId != null && (
            <ExportMenu
              label="Export conversation"
              onExport={(fmt) => exporter.mutate({ fmt })}
              disabled={!msgs.length || exporter.isPending}
            />
          )}
          <IconButton
            label="Sources"
            onClick={() => setSourcesOpen(true)}
            disabled={!focusPassages?.length}
            className={cn("print:hidden", !setupChat && "xl:hidden")}
          >
            <LibraryBig />
          </IconButton>
        </header>

        <div
          ref={threadRef}
          className="min-h-0 flex-1 overflow-y-auto px-4 py-6 md:px-10 print:overflow-visible print:px-0"
        >
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
              const another = printing?.message != null && ![it.q?.id, it.a?.id].includes(printing.message);
              return (
                <div key={it.key} className={cn("flex flex-col gap-5", another && "print:hidden")}>
                  {it.q && (
                    <div className="flex flex-col gap-1.5">
                      <SharedFrom context={it.q.context} />
                      <UserBubble
                        text={it.q.content}
                        files={it.q.attachments ?? []}
                        onEdit={activeId != null && !live ? (t) => editQuestion(it.q!, t) : undefined}
                        versions={
                          (it.q.versions?.count ?? 1) > 1 && (
                            <VersionSwitch
                              versions={it.q.versions}
                              what="question"
                              onPick={live ? undefined : (id) => void switchTo(id)}
                            />
                          )
                        }
                      />
                    </div>
                  )}
                  {memory && it.q && it.a && memory.upto === it.q.id && <SummaryMark summary={memory} />}
                  {it.a?.kind === "research_plan" && it.a.meta?.research != null ? (
                    <ResearchCard id={it.a.meta.research} chatId={activeId} text={it.a.content} />
                  ) : it.a ? (
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
                        .filter((a) => !liveApprovalIds.has(a.id) && a.tool !== "research")
                        .map((a) => ({
                          ...a,
                          estimate: a.estimate as Estimate | null,
                        }))}
                      scope={scope}
                      model={it.a.model ?? model}
                      onRetry={
                        it.q && !live && !it.a.kind && !COMMAND.test(it.q.content)
                          ? () => regenerate(it.q!, it.a!)
                          : undefined
                      }
                      models={llm.models}
                      onRegenerate={
                        it.q && !live && !it.a.kind && !COMMAND.test(it.q.content)
                          ? (m) => regenerate(it.q!, it.a!, m)
                          : undefined
                      }
                      versions={
                        <VersionSwitch
                          versions={it.a.versions}
                          what="answer"
                          onPick={live ? undefined : (id) => void switchTo(id)}
                        />
                      }
                      onAddScope={addScope}
                      check={checks[it.a.id] ?? it.a.check}
                      meta={it.a.meta}
                      onChecked={(c) => setChecks((x) => ({ ...x, [it.a!.id]: c }))}
                      onExport={(fmt) => exporter.mutate({ fmt, message: it.a!.id })}
                      actions={
                        it.a.kind === "research" && it.a.meta?.research != null ? (
                          <ReportActions id={it.a.meta.research} text={it.a.content} passages={it.a.passages ?? []} />
                        ) : undefined
                      }
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
                      onRegenerate={(m) => send(partial.question, m)}
                      {...answerHandlers(aKey)}
                    />
                  ) : (
                    it.q &&
                    !liveHere &&
                    (chat.data?.answering && it === items[items.length - 1] ? (
                      <p role="status" className="m-0 flex items-center gap-2 text-[13px] text-fg-secondary">
                        <Loader2 aria-hidden className="size-3.5 animate-spin" />
                        Working on it. The answer shows here once it’s written.
                      </p>
                    ) : (
                      <p className="m-0 text-[13px] text-fg-muted">
                        No answer was saved for this question. Ask it again to get one.
                      </p>
                    ))
                  )}
                  {memory && (it.a ? memory.upto === it.a.id : memory.upto === it.q?.id) && (
                    <SummaryMark summary={memory} />
                  )}
                </div>
              );
            })}
            {liveHere && (
              <div className="flex flex-col gap-5">
                <UserBubble text={liveHere.question} files={live?.files ?? []} />
                {liveHere.research ? (
                  <ResearchCard id={liveHere.research.id} chatId={activeId ?? live?.chatId ?? null} />
                ) : (
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
                    approvals={liveHere.approvals.filter((a) => a.tool !== "research")}
                    scope={scope}
                    model={model}
                    meta={liveHere.meta}
                    onStop={stop}
                    stopping={stopping != null && stopping === live?.chatId}
                    onRetry={() => send(liveHere.question)}
                    onAddScope={addScope}
                    {...answerHandlers("live")}
                  />
                )}
              </div>
            )}
          </div>
        </div>

        <div className="shrink-0 border-t border-border px-4 pb-4 pt-3 md:px-10 print:hidden">
          <div className="mx-auto flex max-w-[820px] flex-col gap-2.5">
            {noProvider && showThread && (
              <Banner tone="info">
                No LLM provider is set up, so answers list the best passages.{" "}
                {admin && (
                  <Link href="/settings/llm" className="font-bold text-fg-accent hover:underline">
                    Open LLM settings
                  </Link>
                )}
              </Banner>
            )}
            {offer && offer.cid === (activeId ?? live?.chatId) && (
              <NamespaceOffer
                items={offer.items}
                busy={placing}
                onPick={(it) => void pickNamespace(offer.cid, it)}
                onDismiss={() => setOffer(null)}
              />
            )}
            <ScopeBarWithOpen
              scope={scope}
              onChange={setScope}
              model={model}
              tools={llm.known ? llm.tools : null}
              models={llm.models}
              onModel={(m) => void pickModel(m)}
              openTick={scopeOpenTick}
              toolsChip={
                usesTools && !setupChat ? (
                  <ToolsChip value={toolChoice} onChange={pickTools} catalog={toolCatalog.data} admin={admin} />
                ) : undefined
              }
            />
            {queue.length > 0 && (
              <ul className="flex flex-col gap-1" aria-label="Waiting to send">
                {queue.map((m) => (
                  <li key={m.key} className="flex items-center gap-2 text-[12.5px] text-fg-secondary">
                    <Clock className="size-3.5 shrink-0" aria-hidden />
                    <span className="min-w-0 flex-1 truncate">
                      Sends next: {m.text}
                      {m.files.length ? ` (+${m.files.length} file${m.files.length === 1 ? "" : "s"})` : ""}
                    </span>
                    <button
                      type="button"
                      aria-label="Don’t send this"
                      onClick={() => setQueue((q) => q.filter((x) => x.key !== m.key))}
                    >
                      <X className="size-3.5" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <Composer
              value={voice.listening && voice.heard ? voice.heard : draft}
              onChange={setDraft}
              onSend={() => send()}
              onFiles={files.add}
              files={<AttachmentChips items={files.items} onRemove={files.remove} onRetry={files.retry} />}
              hasFiles={files.ready}
              uploading={files.sending}
              busy={Boolean(live)}
              // a new conversation (nothing open, or one asked for): typing goes straight into it
              autoFocus={composing || !showThread}
              placeholder={
                researchOn
                  ? "What should it research?"
                  : voice.listening
                    ? "Listening…"
                    : voice.speaking
                      ? "Reading the answer aloud…"
                      : msgs.length || liveHere
                        ? "Ask a follow-up…"
                        : "Ask across your archive…"
              }
              voice={voice.supported ? { on: voiceOn, onToggle: () => setVoiceOn((v) => !v) } : undefined}
              research={
                setupChat
                  ? undefined
                  : {
                      on: researchOn,
                      onToggle: () => setResearchOn((v) => !v),
                      off:
                        llm.known && (!llm.configured || !llm.tools)
                          ? "Deep research needs a language model that uses tools (Settings › AI assistant)"
                          : files.items.length
                            ? "Deep research takes a goal, not files"
                            : null,
                    }
              }
            />
          </div>
        </div>
      </section>

      <aside
        aria-label="Sources"
        className={cn(
          "hidden min-h-0 overflow-y-auto border-l border-border px-[18px] py-4 print:hidden!",
          !setupChat && "xl:block",
        )}
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
    if (openTick) ref.current?.querySelector<HTMLButtonElement>("button[data-scope-open]")?.click();
  }, [openTick]);
  return (
    <div ref={ref}>
      <ScopeBar {...props} />
    </div>
  );
}
