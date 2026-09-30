"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { RotateCcw, ShieldCheck, Square } from "lucide-react";
import Link from "next/link";
import { useMemo } from "react";

import { Chats, Search } from "@/app/openapi-client";
import type { AnswerCheck, Passage } from "@/app/openapi-client/types.gen";
import { ApprovalCard, type ApprovalView } from "@/components/chat/approval";
import { citedNumbers, isNoModelAnswer, NO_ANSWER, NOTHING_MATCHES, quoteOf } from "@/components/chat/cite";
import { CitationChip } from "@/components/chat/citation";
import { keywords } from "@/components/chat/keywords";
import { RichText } from "@/components/chat/rich-text";
import { scopeWords, type Scope } from "@/components/chat/scope";
import { ToolSteps } from "@/components/chat/steps";
import type { ToolStep, TurnStatus } from "@/components/chat/stream";
import { Button } from "@/components/ui/button";
import { data, useApiClient } from "@/lib/api/browser";
import { plural } from "@/lib/format";
import { useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

/** Where an answer comes from: "3 recordings in podcasts". */
function fromWhere(passages: Passage[] | null): string | null {
  if (!passages?.length) return null;
  const recs = new Set(passages.map((p) => p.recording_id)).size;
  const nss = [...new Set(passages.map((p) => p.namespace).filter(Boolean))] as string[];
  return `${plural(recs, "recording")}${nss.length ? ` in ${nss.join(", ")}` : ""}`;
}

function StateCard({
  tone,
  title,
  children,
  action,
}: {
  tone: "gate" | "red" | "neutral";
  title: string;
  children: React.ReactNode;
  action?: React.ReactNode;
}) {
  const cls = {
    gate: "border-gold-border bg-gold-surface",
    red: "border-red-border bg-red-surface",
    neutral: "border-border bg-surface",
  }[tone];
  return (
    <div
      role={tone === "red" ? "alert" : "status"}
      className={cn("flex max-w-[560px] flex-col gap-2 rounded-md border px-4 py-3.5", cls)}
    >
      <b className={cn("text-[15px] font-bold leading-snug", tone === "red" ? "text-red-dark" : "text-fg")}>{title}</b>
      <div className="text-[13.5px] leading-normal text-fg-strong">{children}</div>
      {action && <div className="pt-1">{action}</div>}
    </div>
  );
}

/** Nothing in this scope answers the question: say where else it may be (namespaces you can read, outside the scope). */
function NothingInScope({
  question,
  scope,
  onAddScope,
}: {
  question: string;
  scope: Scope;
  onAddScope?: (ns: string) => void;
}) {
  const client = useApiClient();
  const { namespaces, roleIn } = useArchive();
  const kw = keywords(question);
  const q = kw.join(" OR ");
  const elsewhere = useQuery({
    queryKey: ["search", q, { elsewhere: true }],
    queryFn: () => data(Search.searchTranscripts({ client, query: { q, limit: 100 } })),
    enabled: kw.length > 0 && Boolean(scope.namespaces?.length),
    staleTime: 60_000,
  });
  const other = useMemo(() => {
    const counts = new Map<string, number>();
    for (const h of elsewhere.data?.hits ?? [])
      if (h.namespace && !scope.namespaces?.includes(h.namespace))
        counts.set(h.namespace, (counts.get(h.namespace) ?? 0) + 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1]).filter(([ns]) => namespaces.some((n) => n.name === ns));
  }, [elsewhere.data, scope.namespaces, namespaces]);
  const best = other[0]?.[0];
  const words = kw.length ? kw.slice(0, 3).join(" or ") : "that";
  return (
    <StateCard
      tone="gate"
      title="Nothing in this scope answers that"
      action={
        best && onAddScope ? (
          <Button size="sm" variant="secondary" onClick={() => onAddScope(best)}>
            Add {best} to scope
          </Button>
        ) : kw.length ? (
          <Button asChild size="sm" variant="secondary">
            <Link href={`/search?q=${encodeURIComponent(q)}`}>Search for these words</Link>
          </Button>
        ) : undefined
      }
    >
      Your scope is {scopeWords(scope)}. Nothing there mentions {words}.
      {best &&
        ` It may be in ${best}, where you’re ${roleIn(best) === "owner" ? "an owner" : `a ${roleIn(best) ?? "viewer"}`}.`}
    </StateCard>
  );
}

export type AnswerProps = {
  chatId: number | null;
  messageId: number | null;
  question: string;
  text: string;
  passages: Passage[] | null;
  status: TurnStatus;
  error?: string | null;
  notice?: string | null;
  steps?: ToolStep[];
  approvals?: ApprovalView[];
  scope: Scope;
  model: string | null;
  hover: number | null;
  onHover: (n: number | null) => void;
  onPreview: (p: Passage) => void;
  onStop?: () => void;
  onRetry?: () => void;
  onAddScope?: (ns: string) => void;
  onShowSources?: () => void;
  active?: boolean;
  check?: AnswerCheck | null;
  onChecked?: (c: AnswerCheck) => void;
};

/** One answer: where it comes from, the tools used, approvals, the text with citations, and its error states. */
export function Answer(p: AnswerProps) {
  const client = useApiClient();
  const { can } = useArchive();
  const streaming = p.status === "streaming";
  const byN = useMemo(() => new Map((p.passages ?? []).map((x) => [x.n, x])), [p.passages]);
  const noModel = isNoModelAnswer(p.text);
  const blank = !p.text || p.text === NO_ANSWER;
  const cites = citedNumbers(p.text);
  const nothing =
    p.status === "done" &&
    !noModel &&
    p.passages?.length === 0 &&
    !p.steps?.length &&
    (p.text === NOTHING_MATCHES || cites.length === 0);
  const failed = p.status === "error" || (p.status === "done" && p.text === NO_ANSWER);
  const check = useMutation({
    mutationFn: () =>
      data(
        Chats.checkMessage({
          client,
          path: { cid: p.chatId as number, mid: p.messageId as number },
        }),
      ),
    onSuccess: (c) => p.onChecked?.(c),
  });
  const result = p.check ?? check.data ?? null;
  const unsupported = (result?.verdicts ?? []).filter((v) => v.supported === false).map((v) => String(v.claim ?? ""));
  const from = fromWhere(p.passages);

  return (
    <article aria-label="Answer" aria-busy={streaming || undefined} className="flex max-w-[720px] flex-col gap-3">
      <div className="flex items-center gap-2 text-[12px] font-medium text-fg-muted">
        <span aria-hidden className={cn("size-2 rounded-full", streaming ? "animate-pulse bg-blue" : "bg-border")} />
        {streaming
          ? from
            ? `Answering from ${from}`
            : `Looking in ${scopeWords(p.scope)}…`
          : from
            ? `From ${from}`
            : "Answer"}
        {p.model ? ` · ${p.model}` : ""}
        {p.onShowSources && p.passages && p.passages.length > 0 && (
          <button
            type="button"
            onClick={p.onShowSources}
            className="ml-1 font-semibold text-fg-accent hover:underline xl:hidden"
          >
            Sources ({p.passages.length})
          </button>
        )}
      </div>

      <ToolSteps steps={p.steps ?? []} working={streaming && Boolean(p.steps?.length) && !p.text} />

      {p.notice && (
        <div
          role="status"
          className="flex max-w-[560px] gap-2.5 rounded-md border border-border bg-surface px-3.5 py-3 text-[13px] leading-snug text-fg-strong"
        >
          <span aria-hidden className="font-bold text-fg-secondary">
            i
          </span>
          <span>
            <b className="text-fg">{p.notice}</b> Questions across many recordings may be incomplete. An admin can pick
            a tool-capable model in Settings → AI.
          </span>
        </div>
      )}

      {(p.approvals ?? []).map((a) => (
        <ApprovalCard key={a.id} a={a} canAct={can("editor")} model={p.model} chatId={p.chatId} />
      ))}

      {noModel ? (
        <div className="flex flex-col gap-2.5">
          <p className="m-0 text-[13px] leading-snug text-fg-secondary">
            No language model is set up, so these are the passages that match best. An admin can add one in Settings →
            LLM provider.
          </p>
          <ul className="m-0 flex list-none flex-col gap-2.5 p-0 font-serif text-[16px] leading-[1.55] text-fg">
            {cites.map((n) => {
              const src = byN.get(n);
              return (
                <li key={n}>
                  <CitationChip
                    n={n}
                    passage={src}
                    active={p.hover === n}
                    onHover={p.onHover}
                    onPreview={p.onPreview}
                  />{" "}
                  {src ? `“${quoteOf(src).text}”` : null}
                </li>
              );
            })}
          </ul>
        </div>
      ) : nothing ? (
        <NothingInScope question={p.question} scope={p.scope} onAddScope={p.onAddScope} />
      ) : failed ? (
        <StateCard
          tone="red"
          title="The model didn’t answer"
          action={
            p.onRetry && (
              <Button size="sm" variant="primary" icon={<RotateCcw />} onClick={p.onRetry}>
                Retry
              </Button>
            )
          }
        >
          {p.error ? `${p.error}. ` : ""}Your question is kept; nothing was lost.
        </StateCard>
      ) : (
        !blank && (
          <div className="font-serif text-[16.5px] leading-[1.6] text-fg [text-wrap:pretty]">
            <RichText
              text={p.text}
              unsupported={unsupported}
              renderCite={(n, key) => (
                <CitationChip
                  key={key}
                  n={n}
                  passage={byN.get(n)}
                  active={p.hover === n}
                  onHover={p.onHover}
                  onPreview={p.onPreview}
                />
              )}
            />
            {streaming && (
              <span aria-hidden className="ml-1 inline-block h-[18px] w-2 translate-y-[3px] bg-fg opacity-60" />
            )}
          </div>
        )
      )}

      {p.status === "stopped" && (
        <p className="m-0 text-[12.5px] text-fg-muted">
          Stopped. What arrived is kept here; ask again for a full answer.
        </p>
      )}

      <div className="flex flex-wrap items-center gap-1.5">
        {streaming && p.onStop && (
          <Button size="sm" variant="ghost" icon={<Square className="!size-3" />} onClick={p.onStop}>
            Stop
          </Button>
        )}
        {p.status === "done" && p.messageId != null && p.chatId != null && cites.length > 0 && !noModel && !result && (
          <Button
            size="sm"
            variant="ghost"
            icon={<ShieldCheck />}
            onClick={() => check.mutate()}
            disabled={check.isPending}
          >
            {check.isPending ? "Checking sources…" : "Check sources"}
          </Button>
        )}
      </div>
      {check.isError && (
        <p role="alert" className="m-0 text-[13px] text-red-dark">
          Couldn’t check the sources: {check.error.message}
        </p>
      )}
      {result && <CheckResult result={result} cited={cites.length} />}
    </article>
  );
}

function CheckResult({ result, cited }: { result: AnswerCheck; cited: number }) {
  const bad = (result.verdicts ?? []).filter((v) => v.supported === false);
  return (
    <div role="status" className="flex flex-col gap-1.5 rounded-md border border-border bg-surface px-3.5 py-3">
      <div className="flex items-center gap-2">
        <ShieldCheck aria-hidden className="size-[15px] text-fg-secondary" />
        <b className="flex-1 text-[13px] font-bold text-fg">
          Checked sources · {result.supported} of {plural(result.claims, "claim")} supported
        </b>
        <span className="text-[11.5px] text-fg-muted">re-read {plural(cited, "cited passage")}</span>
      </div>
      {bad.map((v, i) => (
        <p key={i} className="m-0 text-[12.5px] leading-snug text-fg">
          <b className="text-red-dark">✕ Unsupported:</b> “{String(v.claim ?? "")}”.{v.note ? ` ${String(v.note)}` : ""}
        </p>
      ))}
      {(result.uncited ?? []).length > 0 && (
        <p className="m-0 text-[12.5px] leading-snug text-fg-secondary">
          Not cited: {(result.uncited ?? []).map((u) => `“${u}”`).join(" ")}
        </p>
      )}
    </div>
  );
}
