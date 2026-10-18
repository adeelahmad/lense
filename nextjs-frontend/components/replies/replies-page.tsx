"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { Copy, Mail, Reply as ReplyIcon, RotateCw, Undo2 } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useMemo, useState, type FormEvent, type KeyboardEvent } from "react";

import { Replies } from "@/app/openapi-client";
import type { Reply, ReplyEdit, ReplyEmail } from "@/app/openapi-client/types.gen";
import {
  addresses,
  busy,
  mailtoHref,
  ordered,
  plainText,
  sourceHref,
  statusLine,
  unconfirmed,
} from "@/components/replies/model";
import { REPLIES_KEY, useRepliesChanged } from "@/components/replies/use-replies";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/field";
import { DateTime, EmptyState, PageHeader, SkeletonRows } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { plural } from "@/lib/format";
import { needRole, useArchive } from "@/lib/hooks/session";

/** How long the notice that offers Undo stays. */
export const UNDO_MS = 6000;

const why = (e: unknown) => (e instanceof ApiError ? e.message : "Please try again.");

/** Email replies (docs/email-replies.md): the mail that may need an answer, newest first, each with its draft. A draft
 * is written from your archive, cited [n] to the excerpts below it, with what the archive doesn't say marked to
 * confirm; you edit it, copy it or open it in your mail app. Lens sends nothing. `?email=` brings that email first. */
export function RepliesPage() {
  const client = useApiClient();
  const toast = useToast();
  const changed = useRepliesChanged();
  const asked = Number(useSearchParams().get("email")) || null;
  const { namespace } = useArchive();

  const list = useQuery({
    queryKey: [...REPLIES_KEY, "list", namespace],
    queryFn: () => data(Replies.listReplies({ client, query: { limit: 200, ...(namespace ? { namespace } : {}) } })),
    refetchInterval: (q) => (q.state.data?.emails.some((e) => busy(e.reply)) ? 3000 : false),
  });
  const inList = Boolean(asked && list.data?.emails.some((e) => e.recording === asked));
  // the email you were sent here for, when it isn't one that waits (set aside, automatic, sent)
  const one = useQuery({
    queryKey: [...REPLIES_KEY, "one", asked],
    enabled: asked != null && list.isSuccess && !inList,
    queryFn: () => data(Replies.getReply({ client, path: { rid: asked as number } })),
    refetchInterval: (q) => (busy(q.state.data) ? 3000 : false),
    retry: false,
  });

  const [hidden, setHidden] = useState<Set<number>>(() => new Set());
  const hide = useCallback((id: number, on: boolean) => {
    setHidden((h) => {
      const out = new Set(h);
      if (on) out.add(id);
      else out.delete(id);
      return out;
    });
  }, []);
  // set aside at once, out of the list, with Undo (which brings it back as a draft) instead of a question; done here,
  // not in the card, which leaves the page as it's hidden
  const setAside = useCallback(
    (e: ReplyEmail) => {
      const edit = (status: "draft" | "dismissed") =>
        data(Replies.editReply({ client, path: { rid: e.recording }, body: { status } }));
      hide(e.recording, true);
      edit("dismissed")
        .then(changed)
        .catch((err: unknown) => {
          hide(e.recording, false);
          toast({ tone: "red", title: "Couldn’t set it aside", body: why(err) });
        });
      toast({
        title: `Set aside “${e.subject || "the email"}”`,
        body: "It isn’t waiting any more.",
        duration: UNDO_MS,
        action: {
          label: "Undo",
          onClick: () =>
            void edit("draft")
              .then(() => {
                hide(e.recording, false);
                changed();
              })
              .catch((err: unknown) => toast({ tone: "red", title: "Couldn’t bring it back", body: why(err) })),
        },
      });
    },
    [client, changed, hide, toast],
  );

  const emails = useMemo(() => {
    const all = list.data?.emails ?? [];
    const extra: ReplyEmail[] =
      asked && !inList && one.data
        ? [
            {
              recording: asked,
              subject: (one.data.subject ?? "").replace(/^\s*re\s*:\s*/i, ""),
              from: one.data.to?.[0] ?? null,
              reply: one.data,
            },
          ]
        : [];
    return ordered([...extra, ...all], asked, hidden);
  }, [list.data, asked, inList, one.data, hidden]);
  const drafts = list.data?.drafts ?? 0;

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-5 px-4 py-6 md:px-6">
      <PageHeader
        title="Replies"
        meta={
          list.isSuccess
            ? emails.length
              ? `${plural(emails.length, "email waits", "emails wait")} for an answer${drafts ? `, ${plural(drafts, "draft")} ready` : ""}`
              : "Nothing waiting"
            : undefined
        }
      >
        <p className="m-0 mt-1 text-[13px] text-fg-secondary">
          Drafts are written from your archive, each fact cited to where it came from. Nothing is sent: copy a draft, or
          open it in your mail app.
        </p>
      </PageHeader>
      {list.isPending ? (
        <SkeletonRows rows={3} />
      ) : list.isError ? (
        <EmptyState tone="error" title="Couldn’t read your email">
          {list.error.message}
        </EmptyState>
      ) : emails.length === 0 ? (
        <EmptyState icon={<ReplyIcon />} title="No email waits for an answer">
          Email a watched Gmail or IMAP mailbox brings in shows here, with a reply drafted when you ask for one.{" "}
          <Link href="/sources" className="text-fg-accent hover:underline">
            Watch a mailbox
          </Link>
        </EmptyState>
      ) : (
        <ul className="m-0 flex list-none flex-col gap-3 p-0" aria-label="Email waiting for an answer">
          {emails.map((e, i) => (
            <li key={e.recording}>
              <EmailCard e={e} first={i === 0} onSetAside={setAside} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function EmailCard({ e, first, onSetAside }: { e: ReplyEmail; first: boolean; onSetAside: (e: ReplyEmail) => void }) {
  const client = useApiClient();
  const toast = useToast();
  const changed = useRepliesChanged();
  const { can } = useArchive();
  const canAct = can("editor", e.namespace);
  const r = e.reply;
  const [say, setSay] = useState("");
  const [again, setAgain] = useState(false);

  const draft = useMutation({
    mutationFn: (instructions: string) =>
      data(
        Replies.draftReply({
          client,
          path: { rid: e.recording },
          body: instructions.trim() ? { instructions: instructions.trim() } : {},
        }),
      ),
    onSuccess: () => {
      setSay("");
      setAgain(false);
      changed();
    },
    onError: (err) => toast({ tone: "red", title: "Couldn’t draft a reply", body: why(err) }),
  });
  const edit = useMutation({
    mutationFn: (body: ReplyEdit) => data(Replies.editReply({ client, path: { rid: e.recording }, body })),
    onSuccess: changed,
  });

  const ask = (ev: FormEvent) => {
    ev.preventDefault();
    if (canAct && !draft.isPending) draft.mutate(say);
  };
  const drafted = r.status === "draft" || r.status === "dismissed" || r.status === "sent";
  const asking = drafted ? again : r.status !== "drafting";

  return (
    <article
      aria-label={e.subject || "An email"}
      className="flex flex-col gap-3 rounded-md border border-border bg-background px-4 py-3.5"
    >
      <header className="flex flex-col gap-0.5">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <Link href={`/resources/${e.recording}`} className="text-[15px] font-bold text-fg-strong hover:underline">
            {e.subject || "(no subject)"}
          </Link>
          {e.automatic && <Badge>{e.automatic}</Badge>}
        </div>
        <p className="m-0 flex flex-wrap gap-x-2 text-[12.5px] text-fg-secondary">
          <span>{e.sender || e.from || "Unknown sender"}</span>
          {e.namespace && <span>· {e.namespace}</span>}
          {e.received_at && (
            <span>
              · <DateTime iso={e.received_at} />
            </span>
          )}
        </p>
        {e.snippet && <p className="m-0 mt-1 line-clamp-2 text-[13px] text-fg">{e.snippet}</p>}
      </header>

      {r.status !== "none" && (
        <p role="status" className="m-0 text-[13px] text-fg-secondary">
          {statusLine(r)}
        </p>
      )}

      {drafted && <DraftEditor key={`${r.drafted_at}-${r.updated_at}`} e={e} r={r} canAct={canAct} edit={edit} />}

      {asking && (
        <form onSubmit={ask} className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <Input
            aria-label="What the reply should say"
            placeholder="What should it say? (optional)"
            value={say}
            onChange={(ev) => setSay(ev.target.value)}
            disabled={!canAct}
            autoFocus={first && !drafted}
            className="sm:flex-1"
          />
          <Button
            type="submit"
            variant={drafted ? "secondary" : "primary"}
            size="md"
            icon={drafted ? <RotateCw /> : <ReplyIcon />}
            disabled={!canAct || draft.isPending}
            disabledReason={!canAct ? needRole("editor", e.namespace) : undefined}
          >
            {drafted || r.status === "failed" ? "Draft again" : "Draft a reply"}
          </Button>
        </form>
      )}

      {(drafted || r.status === "drafting" || r.status === "failed") && canAct && (
        <div className="flex flex-wrap gap-2">
          {drafted && !again && (
            <Button size="sm" variant="ghost" icon={<RotateCw />} onClick={() => setAgain(true)}>
              Draft again…
            </Button>
          )}
          {r.status !== "dismissed" && (
            <Button size="sm" variant="ghost" onClick={() => onSetAside(e)} disabled={edit.isPending}>
              Set aside
            </Button>
          )}
          {r.status === "dismissed" && (
            <Button
              size="sm"
              variant="ghost"
              icon={<Undo2 />}
              onClick={() => edit.mutate({ status: "draft" })}
              disabled={edit.isPending}
            >
              Bring it back
            </Button>
          )}
        </div>
      )}
    </article>
  );
}

function DraftEditor({
  e,
  r,
  canAct,
  edit,
}: {
  e: ReplyEmail;
  r: Reply;
  canAct: boolean;
  edit: {
    mutate: (b: ReplyEdit, o?: { onSuccess?: (r: Reply) => void; onError?: (e: Error) => void }) => void;
    isPending: boolean;
  };
}) {
  const toast = useToast();
  const [to, setTo] = useState((r.to ?? []).join(", "));
  const [cc, setCc] = useState((r.cc ?? []).join(", "));
  const [subject, setSubject] = useState(r.subject ?? "");
  const [text, setText] = useState(r.text ?? "");
  const toList = addresses(to);
  const ccList = addresses(cc);
  const wrong = toList.wrong ?? ccList.wrong;
  const dirty =
    text !== (r.text ?? "") ||
    subject !== (r.subject ?? "") ||
    toList.list.join(",") !== (r.to ?? []).join(",") ||
    ccList.list.join(",") !== (r.cc ?? []).join(",");
  const left = unconfirmed(text);
  const others = (r.others ?? []).filter((a) => !ccList.list.includes(a) && !toList.list.includes(a));

  const save = () => {
    if (!dirty || wrong || !canAct) return;
    const before = r.text ?? "";
    edit.mutate(
      { text, subject, to: toList.list, cc: ccList.list },
      {
        onSuccess: () =>
          toast({
            tone: "green",
            title: "Draft saved",
            duration: UNDO_MS,
            action: text !== before ? { label: "Undo", onClick: () => edit.mutate({ text: before }) } : undefined,
          }),
        onError: (err) => toast({ tone: "red", title: "Couldn’t save the draft", body: why(err) }),
      },
    );
  };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(plainText(text));
      toast({ tone: "green", title: "Reply copied", body: "Without its citations, as it would be sent." });
    } catch {
      toast({ tone: "red", title: "Couldn’t copy" });
    }
  };
  const keys = (ev: KeyboardEvent) => {
    if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) {
      ev.preventDefault();
      save();
    }
  };
  const cited = new Set(Array.from(text.matchAll(/\[(\d+)\]/g), (m) => Number(m[1])));

  return (
    <div className="flex flex-col gap-2.5" onKeyDown={keys}>
      <div className="grid grid-cols-[auto_1fr] items-center gap-x-3 gap-y-2 text-[13px]">
        <label htmlFor={`to-${e.recording}`} className="font-bold text-fg-strong">
          To
        </label>
        <Input
          id={`to-${e.recording}`}
          value={to}
          onChange={(ev) => setTo(ev.target.value)}
          invalid={Boolean(toList.wrong)}
          disabled={!canAct}
          className="h-9"
        />
        <label htmlFor={`cc-${e.recording}`} className="font-bold text-fg-strong">
          Cc
        </label>
        <Input
          id={`cc-${e.recording}`}
          value={cc}
          onChange={(ev) => setCc(ev.target.value)}
          invalid={Boolean(ccList.wrong)}
          disabled={!canAct}
          className="h-9"
        />
        <label htmlFor={`subject-${e.recording}`} className="font-bold text-fg-strong">
          Subject
        </label>
        <Input
          id={`subject-${e.recording}`}
          value={subject}
          onChange={(ev) => setSubject(ev.target.value)}
          disabled={!canAct}
          className="h-9"
        />
      </div>
      {others.length > 0 && canAct && (
        <p className="m-0 text-[12.5px] text-fg-secondary">
          Also sent to {others.join(", ")}.{" "}
          <button
            type="button"
            className="text-fg-accent hover:underline"
            onClick={() => setCc([...ccList.list, ...others].join(", "))}
          >
            Reply to all
          </button>
        </p>
      )}
      {wrong && (
        <p role="alert" className="m-0 text-[12.5px] text-red-dark">
          “{wrong}” isn’t an email address.
        </p>
      )}
      <Textarea
        aria-label="The reply"
        value={text}
        onChange={(ev) => setText(ev.target.value)}
        rows={Math.min(16, Math.max(6, text.split("\n").length + 1))}
        disabled={!canAct}
      />
      {left > 0 && (
        <p className="m-0 text-[12.5px] text-gold-dark">
          {plural(left, "thing")} to confirm before it goes: the archive doesn’t say{" "}
          {(r.gaps ?? []).length ? (r.gaps ?? []).join("; ") : "it"}.
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Button asChild variant="primary" size="sm" icon={<Mail />}>
          <a href={mailtoHref({ to: toList.list, cc: ccList.list, subject, text })}>Open in your mail app</a>
        </Button>
        <Button size="sm" icon={<Copy />} onClick={() => void copy()}>
          Copy
        </Button>
        {canAct && (
          <Button size="sm" onClick={save} disabled={!dirty || Boolean(wrong) || edit.isPending}>
            Save
          </Button>
        )}
        {dirty && <span className="text-[12px] text-fg-muted">Ctrl+Enter saves</span>}
      </div>
      {(r.sources ?? []).length > 0 && (
        <details className="text-[13px]" open={citesAny(r)}>
          <summary className="cursor-pointer text-fg-secondary">
            Sources ({(r.sources ?? []).filter((s) => cited.has(s.n)).length} cited)
          </summary>
          <ol className="m-0 mt-1.5 flex list-none flex-col gap-1.5 p-0">
            {(r.sources ?? []).map((s) => (
              <li key={s.n} className={cited.has(s.n) ? "text-fg" : "text-fg-muted"}>
                <span className="font-mono text-[12px]">[{s.n}]</span>{" "}
                <Link href={sourceHref(s)} className="text-fg-accent hover:underline">
                  {s.title || `Resource ${s.recording_id}`}
                  {s.time ? `, ${s.time}` : ""}
                </Link>
                <span className="line-clamp-2 text-[12.5px] text-fg-secondary">{s.text}</span>
              </li>
            ))}
          </ol>
        </details>
      )}
    </div>
  );
}

/** Sources are open while the draft cites any, so what each [n] says is a glance away. */
function citesAny(r: Reply): boolean {
  return (r.sources ?? []).some((s) => s.used);
}
