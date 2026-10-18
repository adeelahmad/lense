"use client";

import { useMutation } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { Chats } from "@/app/openapi-client";
import type { Approval, ApprovalDecision } from "@/app/openapi-client/types.gen";
import { changedParams, draftOf, effectLabel, expiresLine, paramLines, shortValue } from "@/components/approvals/model";
import { useApprovalsChanged } from "@/components/approvals/use-approvals";
import { Button } from "@/components/ui/button";
import { Field, Input, Textarea } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { relative } from "@/lib/format";

const LONG = 80; // a param longer than this is edited in a text box

/** An automation's action waiting for the person's pick: what it would do, why, when it expires, and Approve (Enter
 * while the card has focus), Edit, Always allow and Decline. Decline is the page's, so it can offer Undo. */
export function InboxCard({
  a,
  autoFocus,
  onDone,
  onDecline,
}: {
  a: Approval;
  autoFocus?: boolean;
  onDone: (id: number) => void;
  onDecline: (a: Approval) => void;
}) {
  const client = useApiClient();
  const changed = useApprovalsChanged();
  const toast = useToast();
  const ref = useRef<HTMLDivElement>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<string, string>>(() => draftOf(a.intent?.params));
  const [problem, setProblem] = useState<string | null>(null);
  useEffect(() => {
    if (autoFocus) ref.current?.focus();
  }, [autoFocus]);

  const decide = useMutation({
    mutationFn: (body: ApprovalDecision) => data(Chats.decideApproval({ client, path: { aid: a.id }, body })),
    onSuccess: (out, body) => {
      changed();
      onDone(a.id);
      if (out.status === "failed")
        toast({ tone: "red", title: "Approved, but it failed", body: out.error ?? a.summary });
      else if (body.decision === "always")
        toast({
          tone: "green",
          title: out.always ? "Done, and allowed from now on" : "Done this once",
          body: out.always ? `${a.automation ?? "The automation"} does this without asking from now on.` : out.error,
        });
      else toast({ tone: "green", title: "Done", body: a.summary });
    },
    onError: (e: Error) => setProblem(e.message),
  });

  const can = a.can ?? {};
  const busy = decide.isPending;
  const approve = () => {
    if (can.approve && !busy) decide.mutate({ decision: "approve" });
  };
  const saveEdit = (e: FormEvent) => {
    e.preventDefault();
    try {
      decide.mutate({ decision: "edit", params: changedParams(draft, a.intent?.params) });
    } catch (err) {
      setProblem(err instanceof Error ? err.message : String(err));
    }
  };
  const effect = effectLabel(a.intent?.effect);
  const expires = expiresLine(a.expires_at);
  const lines = paramLines(a.intent?.params);
  const why = a.why;
  const whyHref = why?.url || (why?.activity ? `/timeline?item=${encodeURIComponent(why.activity)}` : null);

  return (
    <div
      ref={ref}
      tabIndex={0}
      role="group"
      aria-label={`Approval: ${a.summary ?? "an action"}`}
      data-approval={a.id}
      onKeyDown={(e) => {
        if (e.key === "Enter" && e.target === e.currentTarget) {
          e.preventDefault();
          approve();
        }
      }}
      className="flex flex-col gap-2.5 rounded-[14px] border-2 border-gold bg-gold-surface p-4 outline-none focus-visible:ring-2 focus-visible:ring-blue"
    >
      <b className="text-[15px] font-bold leading-snug text-fg">{a.summary ?? "An action"}</b>
      <p className="m-0 flex flex-wrap gap-x-2 text-[12.5px] text-fg-secondary">
        {[effect, expires, a.created_at ? `asked ${relative(a.created_at)}` : null].filter(Boolean).join(" · ")}
      </p>
      {why?.summary && (
        <p className="m-0 text-[13.5px] leading-normal text-fg">
          Because:{" "}
          {whyHref ? (
            <Link href={whyHref} className="text-fg-accent hover:underline">
              {shortValue(why.summary, 200)}
            </Link>
          ) : (
            shortValue(why.summary, 200)
          )}
        </p>
      )}
      {!editing && lines.length > 0 && (
        <dl className="m-0 grid grid-cols-[max-content_1fr] gap-x-3 gap-y-0.5 text-[13px]">
          {lines.map((l) => (
            <div key={l.key} className="contents">
              <dt className="font-bold text-fg-strong">{l.key}</dt>
              <dd className="m-0 min-w-0 break-words text-fg">{l.value}</dd>
            </div>
          ))}
        </dl>
      )}
      {editing ? (
        <form onSubmit={saveEdit} className="flex flex-col gap-2.5" aria-label="Change what it does">
          {Object.keys(draft).map((k, i) => (
            <Field key={k} label={k}>
              {({ id }) =>
                draft[k].length > LONG || draft[k].includes("\n") ? (
                  <Textarea
                    id={id}
                    autoFocus={i === 0}
                    value={draft[k]}
                    onChange={(e) => setDraft({ ...draft, [k]: e.target.value })}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) saveEdit(e);
                    }}
                  />
                ) : (
                  <Input
                    id={id}
                    autoFocus={i === 0}
                    value={draft[k]}
                    onChange={(e) => setDraft({ ...draft, [k]: e.target.value })}
                  />
                )
              }
            </Field>
          ))}
          <div className="flex flex-wrap gap-2">
            <Button type="submit" size="sm" variant="approve" disabled={busy}>
              Approve with changes
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => {
                setEditing(false);
                setDraft(draftOf(a.intent?.params));
                ref.current?.focus();
              }}
            >
              Cancel
            </Button>
          </div>
        </form>
      ) : (
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            variant="approve"
            disabled={!can.approve || busy}
            disabledReason="Approve it signed in to Lens"
            onClick={approve}
          >
            Approve
          </Button>
          {can.edit && (
            <Button size="sm" variant="secondary" disabled={busy} onClick={() => setEditing(true)}>
              Edit
            </Button>
          )}
          {can.always && (
            <Button size="sm" variant="secondary" disabled={busy} onClick={() => decide.mutate({ decision: "always" })}>
              Always allow
            </Button>
          )}
          <Button size="sm" variant="ghost" disabled={!can.decline || busy} onClick={() => onDecline(a)}>
            Decline
          </Button>
        </div>
      )}
      {problem && (
        <p role="alert" className="m-0 text-[13px] text-red-dark">
          {problem}
        </p>
      )}
    </div>
  );
}
