"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";

import { Chats } from "@/app/openapi-client";
import type { ApprovalOutcome, Estimate } from "@/app/openapi-client/types.gen";
import { approxCount, money } from "@/components/batches/format";
import { hoursShort } from "@/components/chat/scope";
import { Button } from "@/components/ui/button";
import { data, useApiClient } from "@/lib/api/browser";
import { plural } from "@/lib/format";

export type ApprovalView = {
  id: number;
  tool: string;
  summary?: string | null;
  estimate?: Estimate | null;
  status?: string;
  result?: unknown;
};

function estimateLine(e: Estimate | null | undefined, model: string | null): string | null {
  if (!e) return null;
  const tokens = (e.llm?.input_tokens ?? 0) + (e.llm?.output_tokens ?? 0);
  const parts = [
    plural(e.recordings, "recording"),
    e.hours ? `~${hoursShort(e.hours * 3.6e6)}` : null,
    tokens ? `${approxCount(tokens)} tokens` : null,
    e.llm?.cost != null ? `about ${money(e.llm.cost)}` : null,
  ];
  const m = e.llm?.model ?? model;
  return `${parts.filter(Boolean).join(", ")}${m ? ` on ${m}` : ""}`;
}

function Diamond({ size = 22 }: { size?: number }) {
  return (
    <span
      aria-hidden
      className="inline-block shrink-0 rounded-[5px] bg-gold"
      style={{
        width: size,
        height: size,
        transform: "rotate(45deg) scale(.8)",
      }}
    />
  );
}

function Outcome({ a, outcome }: { a: ApprovalView; outcome: ApprovalOutcome | null }) {
  const status = outcome?.status ?? a.status;
  const result = (outcome ?? (a.result as Record<string, unknown> | null)) as {
    batch?: number | null;
  } | null;
  if (status === "declined")
    return (
      <p className="m-0 flex items-center gap-2 text-[13px] text-fg-secondary">
        <span aria-hidden>–</span> Declined: {a.summary}
      </p>
    );
  return (
    <p className="m-0 flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-fg">
      <span aria-hidden className="font-bold text-green-dark">
        ✓
      </span>
      <span>Approved: {a.summary}</span>
      {result?.batch != null && (
        <Link href={`/batches/${result.batch}`} className="font-bold text-fg-accent hover:underline">
          Batch #{result.batch} · view progress →
        </Link>
      )}
    </p>
  );
}

/**
 * Work the assistant proposed: gold, because a person decides. Runs can start on a sample of three first; entity
 * changes are approved or declined. People who can't change anything see why.
 */
export function ApprovalCard({
  a,
  canAct,
  model,
  chatId,
}: {
  a: ApprovalView;
  canAct: boolean;
  model: string | null;
  chatId: number | null;
}) {
  const client = useApiClient();
  const qc = useQueryClient();
  const decide = useMutation({
    mutationFn: (decision: "approve" | "sample" | "decline") =>
      data(
        Chats.decideApproval({
          client,
          path: { aid: a.id },
          body: { decision },
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["approvals", chatId] }),
  });
  const decided = decide.data ?? null;
  if ((a.status && a.status !== "pending") || decided)
    return (
      <div className="rounded-md border border-border bg-surface px-3.5 py-2.5">
        <Outcome a={a} outcome={decided} />
      </div>
    );
  const run = a.tool === "run_template";
  const reason = canAct ? undefined : "Ask an editor to run this";
  const line = estimateLine(a.estimate, model);
  return (
    <div
      role="group"
      aria-label="Approval needed"
      className="flex flex-col gap-2.5 rounded-[14px] border-2 border-gold bg-gold-surface p-4"
    >
      {!run && <span className="label-caps">Proposed change</span>}
      <div className="flex items-start gap-2.5">
        {run && <Diamond />}
        <b className="flex-1 text-[15px] font-bold leading-snug text-fg">
          {run ? `Approve: ${a.summary}` : `${a.summary}?`}
        </b>
        {a.estimate?.needs_confirmation && (
          <span className="text-[12px] font-medium text-fg-secondary">over the limit in Settings → AI</span>
        )}
      </div>
      {line && (
        <p className="m-0 text-[13.5px] leading-normal text-fg">
          The assistant wants to run this on <b>{line}</b>.
          {a.estimate?.would_replace
            ? ` ${plural(a.estimate.would_replace, "existing output")} would be replaced.`
            : ""}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          variant="approve"
          disabled={!canAct || decide.isPending}
          disabledReason={reason}
          onClick={() => decide.mutate("approve")}
        >
          Approve
        </Button>
        {run && (
          <Button
            size="sm"
            variant="secondary"
            disabled={!canAct || decide.isPending}
            disabledReason={reason}
            onClick={() => decide.mutate("sample")}
          >
            Approve on 3 first
          </Button>
        )}
        <Button
          size="sm"
          variant="ghost"
          disabled={!canAct || decide.isPending}
          disabledReason={reason}
          onClick={() => decide.mutate("decline")}
        >
          Decline
        </Button>
      </div>
      {decide.isError && (
        <p role="alert" className="m-0 text-[13px] text-red-dark">
          {decide.error.message}
        </p>
      )}
    </div>
  );
}
