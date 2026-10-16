"use client";

import { useQuery } from "@tanstack/react-query";
import { Inbox } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Chats } from "@/app/openapi-client";
import type { Approval, Estimate } from "@/app/openapi-client/types.gen";
import { InboxCard } from "@/components/approvals/inbox-card";
import { groupApprovals, outcomeLine, shortValue } from "@/components/approvals/model";
import { INBOX_KEY, useApprovalsChanged } from "@/components/approvals/use-approvals";
import { ApprovalCard } from "@/components/chat/approval";
import { EmptyState, PageHeader, SkeletonRows } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { plural } from "@/lib/format";

/** How long a decline can be taken back before it is sent. */
export const UNDO_MS = 6000;

function without(s: Set<number>, id: number): Set<number> {
  const out = new Set(s);
  out.delete(id);
  return out;
}

/** The approvals inbox (docs/automations.md, Approvals): what waits for your pick, grouped by the automation or
 * conversation that asked, the first in focus so Enter approves it; Decline offers Undo instead of a question; what
 * was decided lately below. `?id=` (the link you were told) brings that one first. */
export function ApprovalsPage() {
  const client = useApiClient();
  const toast = useToast();
  const changed = useApprovalsChanged();
  const asked = Number(useSearchParams().get("id")) || null;

  const waiting = useQuery({
    queryKey: [...INBOX_KEY, "pending"],
    queryFn: () => data(Chats.listApprovals({ client, query: { status: "pending", limit: 200 } })),
  });
  const recent = useQuery({
    queryKey: [...INBOX_KEY, "recent"],
    queryFn: () => data(Chats.listApprovals({ client, query: { limit: 30 } })),
  });
  const inList = Boolean(asked && waiting.data?.some((a) => a.id === asked));
  const one = useQuery({
    queryKey: [...INBOX_KEY, "one", asked],
    enabled: asked != null && waiting.isSuccess && !inList,
    queryFn: () => data(Chats.getApproval({ client, path: { aid: asked as number } })),
    retry: false,
  });

  // declined (Undo still open) or decided here: out of the list at once, before the list is read again
  const [hidden, setHidden] = useState<Set<number>>(() => new Set());
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>());
  const send = useCallback(
    (id: number) => {
      timers.current.delete(id);
      void data(Chats.decideApproval({ client, path: { aid: id }, body: { decision: "decline" } }))
        .then(changed)
        .catch((e: Error) => {
          setHidden((h) => without(h, id));
          toast({ tone: "red", title: "Couldn’t decline it", body: e.message });
        });
    },
    [client, changed, toast],
  );
  const sendRef = useRef(send);
  sendRef.current = send;
  useEffect(() => {
    const pending = timers.current;
    return () => {
      // leaving the page sends what was declined: Undo was only open while the notice was
      for (const [id, t] of pending) {
        clearTimeout(t);
        sendRef.current(id);
      }
    };
  }, []);
  const decline = useCallback(
    (a: Approval) => {
      setHidden((h) => new Set(h).add(a.id));
      timers.current.set(
        a.id,
        setTimeout(() => sendRef.current(a.id), UNDO_MS),
      );
      toast({
        title: `Declined “${shortValue(a.summary ?? "it", 60)}”`,
        body: "Nothing runs.",
        duration: UNDO_MS,
        action: {
          label: "Undo",
          onClick: () => {
            clearTimeout(timers.current.get(a.id));
            timers.current.delete(a.id);
            setHidden((h) => without(h, a.id));
          },
        },
      });
    },
    [toast],
  );
  const done = useCallback((id: number) => setHidden((h) => new Set(h).add(id)), []);

  const items = useMemo(() => {
    const left = (waiting.data ?? []).filter((a) => !hidden.has(a.id));
    return asked ? [...left.filter((a) => a.id === asked), ...left.filter((a) => a.id !== asked)] : left;
  }, [waiting.data, hidden, asked]);
  const groups = useMemo(() => groupApprovals(items), [items]);
  const first = items[0]?.id;
  const decided = (recent.data ?? []).filter((a) => a.status !== "pending" && !hidden.has(a.id));
  const shown = one.data && one.data.status !== "pending" ? one.data : null;

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-4 py-6 md:px-6">
      <PageHeader
        title="Approvals"
        meta={
          waiting.isSuccess
            ? items.length
              ? `${plural(items.length, "waits", "wait")} for your pick`
              : "Nothing waiting"
            : undefined
        }
      >
        <p className="m-0 mt-1 text-[13px] text-fg-secondary">
          Enter approves the one in focus, Tab moves to the next. Declining can be undone for a few seconds.
        </p>
      </PageHeader>
      {shown && (
        <section
          aria-label="The approval you opened"
          className="rounded-md border border-border bg-surface px-3.5 py-2.5"
        >
          <p className="m-0 text-[13.5px] text-fg">{outcomeLine(shown)}</p>
        </section>
      )}
      {waiting.isPending ? (
        <SkeletonRows rows={3} />
      ) : waiting.isError ? (
        <EmptyState tone="error" title="Couldn’t read your approvals">
          {waiting.error.message}
        </EmptyState>
      ) : groups.length === 0 ? (
        <EmptyState icon={<Inbox />} title="Nothing waits for your pick">
          When an automation&apos;s action asks first, it waits here, and you&apos;re told in your chat app.
        </EmptyState>
      ) : (
        groups.map((g) => (
          <section key={g.key} aria-label={g.title} className="flex flex-col gap-2.5">
            <h2 className="m-0 flex items-baseline gap-2 text-[14px] font-bold text-fg-strong">
              {g.title}
              <span className="text-[12.5px] font-normal text-fg-secondary">{g.items.length}</span>
              {g.chat != null && (
                <Link href={`/chat/${g.chat}`} className="text-[12.5px] font-normal text-fg-accent hover:underline">
                  Open the conversation
                </Link>
              )}
            </h2>
            {g.items.map((a) =>
              a.intent ? (
                <InboxCard key={a.id} a={a} autoFocus={a.id === first} onDone={done} onDecline={decline} />
              ) : (
                <ApprovalCard
                  key={a.id}
                  a={{
                    id: a.id,
                    tool: a.tool,
                    summary: a.summary,
                    status: a.status,
                    estimate: (a.estimate as Estimate | null) ?? null,
                  }}
                  canAct={Boolean(a.can?.approve)}
                  model={null}
                  chatId={a.chat ?? null}
                  onDecided={changed}
                />
              ),
            )}
          </section>
        ))
      )}
      {decided.length > 0 && (
        <section aria-label="Decided lately" className="flex flex-col gap-1.5">
          <h2 className="m-0 text-[14px] font-bold text-fg-strong">Decided lately</h2>
          <ul className="m-0 flex list-none flex-col gap-1 p-0">
            {decided.map((a) => (
              <li key={a.id} className="text-[13px] text-fg-secondary">
                {outcomeLine(a)}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
