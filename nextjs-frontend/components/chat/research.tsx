"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookmarkPlus, Check, Download, Minus, Play, Plus, Podcast, Square, Telescope, Volume2, X } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { Chats, Research2 as ResearchApi } from "@/app/openapi-client";
import type { Chat, Passage, ResearchPlan, Research as ResearchRun } from "@/app/openapi-client/types.gen";
import { citedNumbers } from "@/components/chat/cite";
import { RichText } from "@/components/chat/rich-text";
import { Button, IconButton } from "@/components/ui/button";
import { Checkbox, Input } from "@/components/ui/field";
import { Skeleton } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { plural } from "@/lib/format";
import { cn } from "@/lib/utils";
import { useVoice } from "@/lib/voice";

/**
 * Deep research in a conversation (docs/assistant.md, "Deep research"): the plan card a goal's answer shows (its
 * follow-up questions, the sub-questions, where it looks, its tool calls and estimate; Approve, Revise or Decline), the
 * run's progress card, read from GET /research/{id} so it survives a reload, and the report's actions.
 */

type Source = ResearchPlan["sources"][number];

export const SOURCES: { value: Source; label: string }[] = [
  { value: "archive", label: "The archive" },
  { value: "graph", label: "The graph" },
  { value: "notes", label: "Notes" },
  { value: "web", label: "The web" },
  { value: "code", label: "The code sandbox" },
];

const STAGES: Record<string, string> = {
  gather: "Looking things up",
  write: "Writing the report",
  check: "Checking its sources",
  done: "Done",
};

/** A sub-question is at most this long, and a plan has at most this many (research.py). */
const SUB_MAX = 300;
const SUBS_MAX = 8;
const CALLS_MAX = 500;

/** Queued, running or held by a budget: its progress is read again until it ends. */
export const isActive = (status?: string | null) => status === "queued" || status === "running" || status === "held";

/** Dollars as research shows them: small amounts to four places ("$0.0042"), else cents. */
export function usd(v: number | null | undefined): string | null {
  if (v == null) return null;
  return v < 1 ? `$${v.toFixed(4).replace(/0+$/, "").replace(/\.$/, "")}` : `$${v.toFixed(2)}`;
}

/** The plan's estimate for `calls` tool calls: it scales with them, and is always labelled an estimate. */
export function estimateLine(r: ResearchRun, calls: number): string | null {
  const e = r.estimate;
  if (!e) return null;
  const base = e.tool_calls || r.plan.max_tool_calls || calls;
  const cost = e.usd != null ? usd((e.usd * calls) / base) : null;
  return cost ? `about ${cost} (an estimate)` : "no estimate: the model has no price in Settings";
}

/** A run's progress: GET /research/{id}, read again every few seconds while it runs (and now and then while its plan
 * waits, as it may be approved in the inbox or a chat app). */
export function useResearch(id: number | null | undefined) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["research", id],
    queryFn: () => data(ResearchApi.getResearch({ client, path: { rid: id as number } })),
    enabled: id != null,
    staleTime: 2_000,
    refetchInterval: (q) =>
      isActive(q.state.data?.status) ? 3_000 : q.state.data?.status === "planned" ? 20_000 : false,
  });
}

/** Which sources this person can look in now (GET /chats/tools: the tool groups named after them that they have). */
function useSources(chatId: number | null): Set<Source> | null {
  const client = useApiClient();
  const q = useQuery({
    queryKey: ["chat-tools", chatId],
    queryFn: () => data(Chats.chatTools({ client, query: { chat_id: chatId } })),
    staleTime: 60_000,
  });
  if (!q.data) return null;
  const names = new Set(q.data.groups.filter((g) => g.available).map((g) => g.name));
  return new Set(SOURCES.map((s) => s.value).filter((s) => names.has(s)));
}

type Draft = { subs: string[]; sources: Source[]; calls: number; answers: Record<string, string> };

const draftOf = (r: ResearchRun): Draft => ({
  subs: [...r.plan.sub_questions],
  sources: [...r.plan.sources],
  calls: r.plan.max_tool_calls,
  answers: { ...(r.answers ?? {}) },
});

/** What the person changed in the plan: the edits to send with Approve or Revise, or null for none. */
export function planEdits(r: ResearchRun, d: Draft): Partial<ResearchPlan> | null {
  const out: Partial<ResearchPlan> = {};
  const subs = d.subs.map((s) => s.trim()).filter(Boolean);
  if (subs.join("\n") !== r.plan.sub_questions.join("\n")) out.sub_questions = subs;
  if ([...d.sources].sort().join() !== [...r.plan.sources].sort().join()) out.sources = d.sources;
  if (d.calls !== r.plan.max_tool_calls) out.max_tool_calls = d.calls;
  return Object.keys(out).length ? out : null;
}

/** The answers given to the follow-up questions that the plan hasn't been revised with yet. */
export function newAnswers(r: ResearchRun, d: Draft): Record<string, string> {
  return Object.fromEntries(
    Object.entries(d.answers)
      .map(([q, a]) => [q, a.trim()] as const)
      .filter(([q, a]) => a && a !== (r.answers ?? {})[q]),
  );
}

const chip =
  "inline-flex h-8 items-center rounded-pill border px-3 text-[13px] font-semibold transition-colors duration-fast";

/** The plan waiting for approval, editable in place: Enter approves it (or revises it, once a question is answered). */
function PlanCard({ r, chatId }: { r: ResearchRun; chatId: number | null }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const can = useSources(chatId);
  const [d, setD] = useState<Draft>(() => draftOf(r));
  const [added, setAdded] = useState<number | null>(null);
  const edits = planEdits(r, d);
  const answers = newAnswers(r, d);
  const answered = Object.keys(answers).length > 0;
  const filled = d.subs.filter((s) => s.trim()).length;
  const done = (next?: ResearchRun) => {
    if (next) qc.setQueryData(["research", r.id], next);
    else void qc.invalidateQueries({ queryKey: ["research", r.id] });
    void qc.invalidateQueries({ queryKey: ["approvals", chatId] });
  };
  const decide = useMutation({
    mutationFn: (decision: "approve" | "decline") =>
      data(
        Chats.decideApproval({
          client,
          path: { aid: r.approval as number },
          body: decision === "approve" && edits ? { decision, params: edits } : { decision },
        }),
      ),
    onSuccess: () => done(),
  });
  const revise = useMutation({
    mutationFn: () =>
      data(ResearchApi.reviseResearch({ client, path: { rid: r.id }, body: { answers, plan: edits ?? undefined } })),
    onSuccess: (next) => done(next),
  });
  const busy = decide.isPending || revise.isPending;
  const failed = decide.error ?? revise.error;
  const noSub = filled === 0 ? "Keep at least one sub-question" : null;
  const noSource = d.sources.length === 0 ? "Tick at least one place to look" : null;
  const blocked = noSub ?? noSource;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (busy || blocked) return;
    if (answered) revise.mutate();
    else decide.mutate("approve");
  };
  const setSub = (i: number, v: string) => setD({ ...d, subs: d.subs.map((s, j) => (j === i ? v : s)) });
  const step = d.calls >= 20 ? 5 : 1;
  const setCalls = (n: number) =>
    setD({ ...d, calls: Math.max(Math.max(filled, 1), Math.min(CALLS_MAX, Math.round(n) || 0)) });
  const shown = SOURCES.filter((s) => !can || can.has(s.value) || d.sources.includes(s.value));
  const estimate = estimateLine(r, d.calls);
  return (
    <form
      aria-label="Research plan"
      onSubmit={submit}
      className="flex max-w-[720px] flex-col gap-4 rounded-[14px] border-2 border-gold bg-gold-surface p-4"
    >
      <div className="flex items-start gap-2.5">
        <Telescope aria-hidden className="mt-0.5 size-[18px] shrink-0 text-gold-dark" />
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="label-caps">Deep research plan</span>
          <b className="text-[15px] font-bold leading-snug text-fg">{r.plan.title}</b>
        </div>
      </div>

      {(r.questions ?? []).length > 0 && (
        <fieldset className="flex flex-col gap-3">
          <legend className="mb-2 text-[13px] font-bold text-fg-strong">
            A few questions, so it fits what you need
          </legend>
          {(r.questions ?? []).map((q, i) => {
            const given = d.answers[q.question] ?? "";
            const picked = (q.answers ?? []).includes(given);
            const pick = (a: string) => setD({ ...d, answers: { ...d.answers, [q.question]: a } });
            return (
              <div key={i} className="flex flex-col gap-1.5">
                <span id={`research-${r.id}-q${i}`} className="text-[13.5px] leading-snug text-fg">
                  {q.question}
                </span>
                {(q.answers ?? []).length > 0 && (
                  <div role="radiogroup" aria-labelledby={`research-${r.id}-q${i}`} className="flex flex-wrap gap-1.5">
                    {(q.answers ?? []).map((a) => (
                      <button
                        key={a}
                        type="button"
                        role="radio"
                        aria-checked={given === a}
                        onClick={() => pick(given === a ? "" : a)}
                        className={cn(
                          chip,
                          given === a
                            ? "border-blue bg-blue-surface text-fg-accent"
                            : "border-border bg-background text-fg-strong hover:border-fg-muted",
                        )}
                      >
                        {given === a && <Check aria-hidden className="mr-1 size-3.5" />}
                        {a}
                      </button>
                    ))}
                  </div>
                )}
                <Input
                  value={picked ? "" : given}
                  onChange={(e) => pick(e.target.value)}
                  placeholder={(q.answers ?? []).length ? "Or say something else" : "Your answer"}
                  aria-label={`Your answer: ${q.question}`}
                  maxLength={SUB_MAX}
                  className="h-9 text-[14px]"
                />
              </div>
            );
          })}
        </fieldset>
      )}

      <fieldset className="flex flex-col gap-1.5">
        <legend className="mb-2 text-[13px] font-bold text-fg-strong">What it answers, in order</legend>
        <ol className="m-0 flex list-none flex-col gap-1.5 p-0">
          {d.subs.map((s, i) => (
            <li key={i} className="flex items-center gap-1.5">
              <span aria-hidden className="w-5 shrink-0 text-right text-[12px] tabular-nums text-fg-muted">
                {i + 1}.
              </span>
              <Input
                value={s}
                onChange={(e) => setSub(i, e.target.value)}
                aria-label={`Sub-question ${i + 1}`}
                autoFocus={added === i}
                maxLength={SUB_MAX}
                className="h-9 min-w-0 flex-1 text-[14px]"
              />
              <IconButton
                label={`Remove sub-question ${i + 1}`}
                size={32}
                disabled={d.subs.length <= 1}
                onClick={() => setD({ ...d, subs: d.subs.filter((_, j) => j !== i) })}
              >
                <X />
              </IconButton>
            </li>
          ))}
        </ol>
        {d.subs.length < SUBS_MAX && (
          <Button
            size="xs"
            variant="ghost"
            icon={<Plus />}
            className="self-start"
            onClick={() => {
              setAdded(d.subs.length);
              setD({ ...d, subs: [...d.subs, ""] });
            }}
          >
            Add a sub-question
          </Button>
        )}
      </fieldset>

      <fieldset className="flex flex-col gap-2">
        <legend className="mb-2 text-[13px] font-bold text-fg-strong">Where it looks</legend>
        <div className="flex flex-wrap gap-x-5 gap-y-2">
          {shown.map((s) => (
            <Checkbox
              key={s.value}
              label={s.label}
              checked={d.sources.includes(s.value)}
              onCheckedChange={(on) =>
                setD({
                  ...d,
                  sources: on ? [...d.sources, s.value] : d.sources.filter((x) => x !== s.value),
                })
              }
            />
          ))}
        </div>
      </fieldset>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <span id={`research-${r.id}-calls`} className="text-[13px] font-bold text-fg-strong">
          Tool calls, at most
        </span>
        <div
          role="group"
          aria-labelledby={`research-${r.id}-calls`}
          className="inline-flex items-center rounded-pill border border-border bg-background"
        >
          <IconButton
            label="Fewer tool calls"
            size={32}
            disabled={d.calls <= Math.max(filled, 1)}
            onClick={() => setCalls(d.calls - step)}
          >
            <Minus />
          </IconButton>
          <input
            type="number"
            inputMode="numeric"
            aria-label="Most tool calls"
            value={d.calls}
            min={Math.max(filled, 1)}
            max={CALLS_MAX}
            onChange={(e) => setCalls(Number(e.target.value))}
            className="w-12 bg-transparent text-center text-[14px] font-semibold tabular-nums text-fg outline-hidden [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none"
          />
          <IconButton
            label="More tool calls"
            size={32}
            disabled={d.calls >= CALLS_MAX}
            onClick={() => setCalls(d.calls + step)}
          >
            <Plus />
          </IconButton>
        </div>
        {estimate && (
          <span className="text-[13px] text-fg-secondary" title={r.estimate?.basis ?? undefined}>
            {estimate}
          </span>
        )}
      </div>

      {(r.plan.report_outline ?? []).length > 0 && (
        <p className="m-0 text-[13px] leading-snug text-fg-secondary">
          The report: {(r.plan.report_outline ?? []).join("; ")}.
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {answered ? (
          <>
            <Button
              type="submit"
              size="sm"
              variant="primary"
              disabled={busy || Boolean(blocked)}
              disabledReason={blocked}
            >
              {revise.isPending ? "Revising…" : "Revise with my answers"}
            </Button>
            <Button
              size="sm"
              variant="secondary"
              disabled={busy || Boolean(blocked)}
              disabledReason={blocked}
              onClick={() => decide.mutate("approve")}
            >
              Approve as it is
            </Button>
          </>
        ) : (
          <>
            <Button
              type="submit"
              size="sm"
              variant="approve"
              disabled={busy || Boolean(blocked)}
              disabledReason={blocked}
            >
              {decide.isPending ? "Starting…" : edits ? "Approve with my edits" : "Approve"}
            </Button>
            <Button
              size="sm"
              variant="secondary"
              disabled={busy || !edits || Boolean(blocked)}
              disabledReason={blocked ?? (edits ? undefined : "Answer a question or edit the plan first")}
              onClick={() => revise.mutate()}
            >
              {revise.isPending ? "Revising…" : "Revise"}
            </Button>
          </>
        )}
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => decide.mutate("decline")}>
          Decline
        </Button>
        <span className="hidden text-[12px] text-fg-muted sm:inline">
          Enter {answered ? "revises it" : "approves it"}
        </span>
      </div>
      {failed && (
        <p role="alert" className="m-0 text-[13px] text-red-dark">
          {failed.message}
        </p>
      )}
    </form>
  );
}

/** What a run that ended says about how it ended. */
function endedLine(r: ResearchRun): string {
  if (r.status === "declined") return "Declined: no research was done";
  if (r.status === "failed") return `It stopped: ${r.error ?? "something went wrong"}. What it found is below.`;
  if (r.status === "stopped" || r.ended === "stopped") return "Stopped: what it found is below";
  if (r.ended === "timeout") return "Out of time (Settings › AI assistant): the report has what it found";
  if (r.ended === "budget") return "A budget ended it early: the report has what it found";
  return "Done: the report is below";
}

/** The run, from approval to its report: its stage, the sub-questions done, sources, tool calls, cost and time. */
function ProgressCard({ r }: { r: ResearchRun }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const set = (next?: ResearchRun) =>
    next ? qc.setQueryData(["research", r.id], next) : void qc.invalidateQueries({ queryKey: ["research", r.id] });
  const stop = useMutation({
    mutationFn: () => data(ResearchApi.stopResearch({ client, path: { rid: r.id } })),
    onSuccess: () => set(),
  });
  const resume = useMutation({
    mutationFn: () => data(ResearchApi.resumeResearch({ client, path: { rid: r.id } })),
    onSuccess: (next) => set(next),
  });
  const going = isActive(r.status);
  const p = r.progress;
  const subs = r.plan.sub_questions;
  const total = p.sub_questions || subs.length;
  const done = Math.min(p.done ?? 0, total);
  const answered = new Set((r.findings ?? []).map((f) => f.question));
  const headline =
    r.status === "queued"
      ? "Waiting its turn: another research of yours is running"
      : r.status === "held"
        ? `Paused: ${r.held?.why ?? "a budget is spent"}`
        : r.status === "running"
          ? `${STAGES[r.stage ?? "gather"] ?? "Working"}…`
          : endedLine(r);
  const cost = usd(p.cost_usd);
  const facts = [
    `${done} of ${plural(total, "sub-question")}`,
    plural(p.sources ?? 0, "source"),
    `${p.tool_calls ?? 0} of ${plural(r.plan.max_tool_calls, "tool call")}`,
    cost ? `${cost}${p.cost_estimated ? " (an estimate)" : ""}` : null,
    p.minutes ? `${p.minutes} min` : null,
  ].filter(Boolean);
  const stopping = stop.isPending || (stop.isSuccess && going);
  return (
    <section
      aria-label="Research progress"
      className={cn(
        "flex max-w-[720px] flex-col gap-3 rounded-[14px] border p-4",
        going ? "border-blue-border bg-blue-surface" : "border-border bg-surface",
      )}
    >
      <div className="flex items-start gap-2.5">
        <Telescope aria-hidden className={cn("mt-0.5 size-[18px] shrink-0", going ? "text-blue" : "text-fg-muted")} />
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="label-caps">Deep research</span>
          <b className="text-[15px] font-bold leading-snug text-fg">{r.plan.title}</b>
        </div>
      </div>
      <p aria-live="polite" className="m-0 flex items-center gap-2 text-[13.5px] font-semibold text-fg">
        {going && (
          <span
            aria-hidden
            className={cn("size-2 shrink-0 rounded-full", r.status === "running" ? "animate-pulse bg-blue" : "bg-gold")}
          />
        )}
        {headline}
      </p>
      {r.status !== "declined" && (
        <>
          <div
            role="progressbar"
            aria-label="Sub-questions answered"
            aria-valuemin={0}
            aria-valuemax={total}
            aria-valuenow={done}
            className="h-1.5 overflow-hidden rounded-full bg-border"
          >
            <div
              className={cn("h-full rounded-full transition-[width] duration-base", going ? "bg-blue" : "bg-fg-muted")}
              style={{ width: `${total ? Math.round((done / total) * 100) : 0}%` }}
            />
          </div>
          <p className="m-0 text-[12.5px] leading-snug text-fg-secondary">{facts.join(" · ")}</p>
          <ol className="m-0 flex list-none flex-col gap-1 p-0">
            {subs.map((s, i) => {
              const ok = answered.has(s);
              const now = r.status === "running" && r.stage === "gather" && !ok && i === done;
              return (
                <li
                  key={i}
                  className={cn(
                    "flex items-start gap-2 text-[13px] leading-snug",
                    ok ? "text-fg" : now ? "font-semibold text-fg" : "text-fg-muted",
                  )}
                >
                  <span aria-hidden className="w-4 shrink-0 text-center">
                    {ok ? <Check className="inline size-3.5 text-green-dark" /> : now ? "›" : "·"}
                  </span>
                  <span className="min-w-0 flex-1">
                    {s}
                    <span className="sr-only">{ok ? " (answered)" : now ? " (looking it up)" : ""}</span>
                  </span>
                </li>
              );
            })}
          </ol>
        </>
      )}
      {going && (
        <div className="flex flex-wrap gap-2">
          {r.status === "held" && (
            <Button
              size="sm"
              variant="approve"
              icon={<Play />}
              disabled={resume.isPending}
              onClick={() => resume.mutate()}
            >
              Go on anyway
            </Button>
          )}
          <Button
            size="sm"
            variant="ghost"
            icon={<Square className="size-3!" />}
            disabled={stopping}
            disabledReason="Stopping after the step it’s on"
            onClick={() => stop.mutate()}
          >
            {stopping ? "Stopping…" : "Stop"}
          </Button>
        </div>
      )}
      {(r.notes ?? []).length > 0 && (
        <p className="m-0 text-[12.5px] text-fg-secondary">
          Saved as{" "}
          {(r.notes ?? []).map((n, i) => (
            <span key={n}>
              {i > 0 && ", "}
              <Link href={`/notes/${n}`} className="font-semibold text-fg-accent hover:underline">
                a note
              </Link>
            </span>
          ))}
          .
        </p>
      )}
      {(stop.error ?? resume.error) && (
        <p role="alert" className="m-0 text-[13px] text-red-dark">
          {(stop.error ?? resume.error)?.message}
        </p>
      )}
    </section>
  );
}

/**
 * The card a research goal's answer shows: its plan while it waits for approval, then the run's progress. `text` is
 * the plan as the conversation saved it, shown when the research can't be read.
 */
export function ResearchCard({ id, chatId, text }: { id: number; chatId: number | null; text?: string }) {
  const q = useResearch(id);
  const qc = useQueryClient();
  // the report is saved in the conversation when the run ends: read the conversation again to show it
  const answer = q.data?.answer;
  const asked = useRef(new Set<number>());
  useEffect(() => {
    if (answer == null || chatId == null || asked.current.has(answer)) return;
    const shown = qc.getQueryData<Chat>(["chat", chatId]);
    if (shown && !(shown.messages ?? []).some((m) => m.id === answer)) {
      asked.current.add(answer);
      void qc.invalidateQueries({ queryKey: ["chat", chatId] });
    }
  }, [answer, chatId, qc]);
  if (q.isLoading)
    return (
      <div aria-busy="true" aria-label="Loading the research" className="flex max-w-[720px] flex-col gap-2">
        <Skeleton className="w-[60%]" />
        <Skeleton className="w-[80%]" />
      </div>
    );
  if (!q.data)
    return (
      <div className="flex max-w-[720px] flex-col gap-2">
        <p role="alert" className="m-0 text-[13px] text-red-dark">
          Couldn’t read this research{q.error ? `: ${q.error.message}` : ""}.
        </p>
        {text && (
          <div className="text-[14.5px] leading-[1.55] text-fg">
            <RichText text={text} renderCite={() => null} />
          </div>
        )}
      </div>
    );
  const r = q.data;
  return r.status === "planned" && r.approval != null ? (
    <PlanCard key={`${r.id}:${r.revisions ?? 0}`} r={r} chatId={chatId} />
  ) : (
    <ProgressCard r={r} />
  );
}

/** The report as Markdown, with the sources it cites under it, for a file. */
export function reportMarkdown(title: string, text: string, passages: Passage[]): string {
  const cited = new Set(citedNumbers(text));
  const used = passages.filter((p) => cited.has(p.n));
  const lines = [`# ${title}`, "", text.trim()];
  if (used.length) {
    lines.push("", "## Sources", "");
    for (const p of used) {
      const where = p.url
        ? `${p.title || p.site || p.url} <${p.url}>`
        : [p.title || "Asset", p.time ? `at ${p.time}` : null, p.speaker].filter(Boolean).join(", ");
      lines.push(`- [${p.n}] ${where}`);
    }
  }
  return `${lines.join("\n")}\n`;
}

function save(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/markdown;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

const fileName = (title: string) =>
  `${
    title
      .replace(/[\\/:*?"<>|]+/g, " ")
      .trim()
      .slice(0, 80) || "Research"
  }.md`;

/** What can be done with a research report: save it as a note, hear it, make a podcast episode from what it cites,
 * or download it as Markdown. */
export function ReportActions({ id, text, passages }: { id: number; text: string; passages: Passage[] }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const router = useRouter();
  const voice = useVoice();
  const r = useResearch(id);
  const title = r.data?.plan.title ?? "Research";
  const [spaces, setSpaces] = useState<string[] | null>(null);
  const [reading, setReading] = useState(false);
  const note = useMutation({
    mutationFn: (namespace?: string) =>
      data(ResearchApi.researchNote({ client, path: { rid: id }, body: namespace ? { namespace } : {} })),
    onSuccess: (made) => {
      setSpaces(null);
      void qc.invalidateQueries({ queryKey: ["research", id] });
      toast({
        title: `Saved as a note in ${made.namespace}`,
        tone: "green",
        action: { label: "Open", onClick: () => router.push(`/notes/${made.page}`) },
      });
    },
    onError: (e) => {
      const several = /^pick a namespace: (.+)$/.exec(e.message);
      if (several) setSpaces(several[1].split(", "));
    },
  });
  const podcast = useMutation({
    mutationFn: () => data(ResearchApi.researchPodcast({ client, path: { rid: id }, body: {} })),
    onSuccess: (s) =>
      toast({
        title: "Making a podcast episode",
        body: `From the assets the report cites, in ${s.namespace}.`,
        tone: "green",
        action: { label: "Open", onClick: () => router.push(`/podcasts/${s.episode}`) },
      }),
  });
  const hear = async () => {
    if (reading) {
      voice.stop();
      return;
    }
    setReading(true);
    try {
      await voice.speak(text);
    } finally {
      setReading(false);
    }
  };
  const failed = note.error && !spaces ? note.error : podcast.error;
  return (
    <div className="flex flex-col gap-2">
      <div role="group" aria-label="Report actions" className="flex flex-wrap items-center gap-1.5">
        <Button
          size="sm"
          variant="secondary"
          icon={<BookmarkPlus />}
          disabled={note.isPending}
          onClick={() => note.mutate(undefined)}
        >
          {note.isPending ? "Saving…" : "Save as note"}
        </Button>
        <Button size="sm" variant="ghost" icon={reading ? <Square className="size-3!" /> : <Volume2 />} onClick={hear}>
          {reading ? "Stop reading" : "Read aloud"}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          icon={<Podcast />}
          disabled={podcast.isPending}
          onClick={() => podcast.mutate()}
        >
          {podcast.isPending ? "Starting…" : "Make a podcast"}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          icon={<Download />}
          onClick={() => save(fileName(title), reportMarkdown(title, text, passages))}
        >
          Export
        </Button>
      </div>
      {spaces && (
        <div role="group" aria-label="Save the note in" className="flex flex-wrap items-center gap-1.5">
          <span className="text-[13px] text-fg-secondary">Save it in</span>
          {spaces.map((ns) => (
            <button
              key={ns}
              type="button"
              disabled={note.isPending}
              onClick={() => note.mutate(ns)}
              className={cn(chip, "border-border bg-background text-fg-strong hover:border-fg-muted")}
            >
              {ns}
            </button>
          ))}
          <IconButton label="Don’t save it" size={28} onClick={() => setSpaces(null)}>
            <X />
          </IconButton>
        </div>
      )}
      {failed && (
        <p role="alert" className="m-0 text-[13px] text-red-dark">
          {failed.message}
        </p>
      )}
    </div>
  );
}
