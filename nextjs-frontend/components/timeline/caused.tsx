"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";

import { Activities, Chats } from "@/app/openapi-client";
import type { Approval, ApprovalDecision, Caused as CausedOut } from "@/app/openapi-client/types.gen";
import { shortValue } from "@/components/approvals/model";
import { useApprovalsChanged } from "@/components/approvals/use-approvals";
import { runCounts } from "@/components/automations/detail-tabs";
import { runStatus } from "@/components/automations/model";
import type { Activity } from "@/components/timeline/model";
import { Badge, type Tone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";

/** What an activity caused (GET /api/v1/activities/{id}/caused): the runs it started and the approvals they asked
 * for; read when the item is opened, and again after a pick. */
export function useCaused(id: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["timeline-caused", id],
    queryFn: () => data(Activities.activityCaused({ client, path: { activity_id: id } })) as Promise<CausedOut>,
    staleTime: 10_000,
  });
}

/** An approval's status, as its badge says it. */
export function approvalStatus(s: string | null | undefined): { label: string; tone: Tone } {
  const known: Record<string, { label: string; tone: Tone }> = {
    pending: { label: "Waiting for you", tone: "gate" },
    running: { label: "Running", tone: "intent" },
    done: { label: "Done", tone: "green" },
    failed: { label: "Failed", tone: "red" },
    declined: { label: "Declined", tone: "neutral" },
    expired: { label: "Expired", tone: "neutral" },
  };
  return known[s ?? ""] ?? { label: s ?? "—", tone: "neutral" };
}

/** Whether the item is an approval's own line on the timeline (approval.waiting), whose approval is what it is about. */
export function isOwnApproval(a: Pick<Activity, "kind">): boolean {
  return a.kind === "approval.waiting";
}

/** What an opened item caused: each automation run it started (a link to the automation's runs, with its status)
 * and each approval those asked for, with Approve and Decline on the ones still waiting, so a pick is made where
 * it was caused. An approval's own line lists that approval. Nothing is shown while there is nothing. */
export function Caused({ a }: { a: Activity }) {
  const got = useCaused(a.id);
  const runs = got.data?.runs ?? [];
  const approvals = got.data?.approvals ?? [];
  if (!runs.length && !approvals.length) return null;
  const own = isOwnApproval(a);
  return (
    <div className="flex flex-col gap-1" data-testid="caused">
      <span className="font-semibold text-fg-secondary">{own ? "Your pick" : "What this caused"}</span>
      {runs.length > 0 && (
        <ul className="m-0 flex list-none flex-col gap-0.5 p-0">
          {runs.map((r) => {
            const st = runStatus(r.status);
            return (
              <li key={r.id} className="flex flex-wrap items-center gap-2">
                <Link href={`/automations/${r.automation}?tab=runs`} className="text-fg hover:underline">
                  {r.name || `Automation ${r.automation}`}
                </Link>
                <Badge tone={st.tone}>{st.label}</Badge>
                <span className="text-fg-muted">{runCounts(r)}</span>
              </li>
            );
          })}
        </ul>
      )}
      {approvals.length > 0 && (
        <ul className="m-0 flex list-none flex-col gap-1 p-0">
          {approvals.map((ap) => (
            <ApprovalLine key={ap.id} a={ap} />
          ))}
        </ul>
      )}
    </div>
  );
}

/** One approval an item led to: what it would do, its status, and Approve and Decline while it waits. */
function ApprovalLine({ a }: { a: Approval }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const changed = useApprovalsChanged();
  const toast = useToast();
  const st = approvalStatus(a.status);
  const can = a.can ?? {};
  const decide = useMutation({
    mutationFn: (body: ApprovalDecision) => data(Chats.decideApproval({ client, path: { aid: a.id }, body })),
    onSuccess: (out, body) => {
      changed();
      void qc.invalidateQueries({ queryKey: ["timeline-caused"] });
      void qc.invalidateQueries({ queryKey: ["timeline"] });
      if (body.decision === "decline")
        toast({ title: `Declined “${shortValue(a.summary ?? "it", 60)}”`, body: "Nothing runs." });
      else if (out.status === "failed")
        toast({ tone: "red", title: "Approved, but it failed", body: out.error ?? a.summary });
      else toast({ tone: "green", title: "Done", body: a.summary });
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t decide it", body: e.message }),
  });
  const busy = decide.isPending;
  return (
    <li className="flex flex-wrap items-center gap-2" data-approval={a.id}>
      <Link href={`/approvals?id=${a.id}`} className="min-w-0 text-fg hover:underline">
        {shortValue(a.summary ?? "An action", 160)}
      </Link>
      {a.automation && <span className="text-fg-muted">by {a.automation}</span>}
      <Badge tone={st.tone}>{st.label}</Badge>
      {a.status === "pending" && (
        <>
          <Button
            size="xs"
            variant="approve"
            disabled={!can.approve || busy}
            disabledReason="Approve it signed in to Lens"
            onClick={() => decide.mutate({ decision: "approve" })}
          >
            Approve
          </Button>
          <Button
            size="xs"
            variant="ghost"
            disabled={!can.decline || busy}
            onClick={() => decide.mutate({ decision: "decline" })}
          >
            Decline
          </Button>
        </>
      )}
    </li>
  );
}
