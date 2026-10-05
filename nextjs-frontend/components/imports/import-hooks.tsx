"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Plus, Webhook } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { ImportHooks } from "@/app/openapi-client";
import type { ImportHook, ImportHookUpdate } from "@/app/openapi-client/types.gen";
import { isUnreachable } from "@/components/errors/error-states";
import { Badge } from "@/components/ui/badge";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input } from "@/components/ui/field";
import { CodeBlock, EmptyState, SkeletonRows } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { needRole } from "@/lib/hooks/session";

const PUSH = "/api/v1/hooks/import";
const hooksKey = (ns: string) => ["namespace", ns, "import-hooks"] as const;

function origin() {
  return typeof window === "undefined" ? "" : window.location.origin;
}

/** Ready-to-run examples for a token: a file as the body, and a web address as JSON. */
export function hookExamples(base: string, token: string) {
  const url = `${base}${PUSH}`;
  return {
    file: `curl -H "Authorization: Bearer ${token}" --data-binary @notes.pdf "${url}?filename=notes.pdf"`,
    link: `curl -H "Authorization: Bearer ${token}" -H "Content-Type: application/json" -d '{"url": "https://example.org/report.pdf"}' ${url}`,
  };
}

/** When a hook last brought something in, in words. */
export function lastUsed(h: Pick<ImportHook, "used" | "last_used_at">) {
  if (!h.used || !h.last_used_at) return "Nothing pushed yet";
  const when = new Date(h.last_used_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  return `${h.used} imported, last ${when}`;
}

function useImportHooks(ns: string, enabled: boolean) {
  const client = useApiClient();
  return useQuery({
    queryKey: hooksKey(ns),
    queryFn: () => data(ImportHooks.listImportHooks({ client, path: { name: ns } })),
    enabled,
  });
}

/**
 * A namespace's import webhooks (docs/api.md#import-webhooks), on its page: addresses other services push files,
 * web addresses or text to, which land in this namespace. Owners make, pause, re-key and delete them.
 */
export function ImportHooksSection({ ns, isOwner }: { ns: string; isOwner: boolean }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const q = useImportHooks(ns, isOwner);
  const [making, setMaking] = useState(false);
  const [shown, setShown] = useState<{ name: string; token: string } | null>(null);
  const [rekeying, setRekeying] = useState<ImportHook | null>(null);
  const [deleting, setDeleting] = useState<ImportHook | null>(null);
  const refresh = () => void qc.invalidateQueries({ queryKey: hooksKey(ns) });
  const fail = (title: string) => (e: Error) => toast({ title, body: e.message, tone: "red" });

  const update = useMutation({
    mutationFn: ({ h, body }: { h: ImportHook; body: ImportHookUpdate }) =>
      data(ImportHooks.updateImportHook({ client, path: { name: ns, hid: h.id }, body })),
    onSuccess: (h) => {
      refresh();
      toast({ title: h.enabled ? "Hook resumed" : "Hook paused", body: h.name, tone: h.enabled ? "green" : undefined });
    },
    onError: fail("Couldn’t change the hook"),
  });
  const rekey = useMutation({
    mutationFn: (h: ImportHook) => data(ImportHooks.newImportHookToken({ client, path: { name: ns, hid: h.id } })),
    onSuccess: (r, h) => {
      setRekeying(null);
      setShown({ name: h.name, token: r.token });
      refresh();
    },
    onError: fail("Couldn’t make a token"),
  });
  const remove = useMutation({
    mutationFn: (h: ImportHook) => data(ImportHooks.deleteImportHook({ client, path: { name: ns, hid: h.id } })),
    onSuccess: (_, h) => {
      setDeleting(null);
      refresh();
      toast({ title: "Hook deleted", body: `${h.name} can’t push to ${ns} any more.` });
    },
    onError: fail("Couldn’t delete the hook"),
  });

  return (
    <section
      id="import-hooks"
      aria-labelledby="import-hooks-heading"
      className="flex scroll-mt-4 flex-col gap-3.5 rounded-md border border-border bg-background px-4 py-5 sm:px-6"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 flex-1 basis-[260px] flex-col gap-1">
          <h2 id="import-hooks-heading" className="text-[17px] font-bold leading-tight text-fg">
            Import webhooks
          </h2>
          <p className="text-[13px] leading-[1.45] text-fg-secondary">
            Let a scanner, a phone shortcut or an automation tool push files, links or text straight into {ns}. Each
            hook has its own token, so you can stop one without touching the others.
          </p>
        </div>
        <Button
          size="sm"
          icon={<Plus />}
          disabled={!isOwner}
          disabledReason={needRole("owner", ns)}
          onClick={() => setMaking(true)}
        >
          New webhook
        </Button>
      </div>
      {!isOwner ? null : q.isPending ? (
        <SkeletonRows rows={2} />
      ) : q.isError ? (
        <EmptyState
          tone="error"
          icon={<Webhook />}
          title={isUnreachable(q.error) ? "Can’t reach the server" : "Couldn’t load the webhooks"}
          actions={<Button onClick={() => q.refetch()}>Try again</Button>}
        >
          {q.error.message}
        </EmptyState>
      ) : q.data.length ? (
        <ul aria-label="Import webhooks" className="flex flex-col">
          {q.data.map((h) => (
            <li key={h.id} className="flex flex-wrap items-start gap-x-3 gap-y-2 border-t border-border py-2.5">
              <span className="grid size-8 shrink-0 place-items-center rounded-sm bg-surface-neutral text-fg-secondary">
                <Webhook aria-hidden className="size-4" />
              </span>
              <span className="flex min-w-0 flex-1 basis-[200px] flex-col gap-0.5">
                <span className="flex flex-wrap items-center gap-2">
                  <b className="text-[13.5px] font-semibold text-fg">{h.name}</b>
                  {!h.enabled && <Badge tone="gate">Paused</Badge>}
                </span>
                <span className="font-mono text-[12px] text-fg-secondary">Token ending …{h.token_tail ?? "????"}</span>
                <span className="text-[12px] text-fg-muted">{lastUsed(h)}</span>
              </span>
              <span className="flex flex-wrap gap-1.5">
                <Button
                  size="xs"
                  variant="ghost"
                  disabled={update.isPending}
                  onClick={() => update.mutate({ h, body: { enabled: !h.enabled } })}
                  aria-label={`${h.enabled ? "Pause" : "Resume"} ${h.name}`}
                >
                  {h.enabled ? "Pause" : "Resume"}
                </Button>
                <Button size="xs" variant="ghost" onClick={() => setRekeying(h)} aria-label={`New token for ${h.name}`}>
                  New token
                </Button>
                <Button size="xs" variant="danger-ghost" onClick={() => setDeleting(h)} aria-label={`Delete ${h.name}`}>
                  Delete
                </Button>
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="border-t border-border pt-3 text-[13px] text-fg-muted">No import webhooks yet.</p>
      )}

      <NewHookDialog
        ns={ns}
        open={making}
        onClose={() => setMaking(false)}
        onMade={(name, token) => {
          setMaking(false);
          setShown({ name, token });
          refresh();
        }}
      />
      <Dialog
        open={Boolean(shown)}
        onOpenChange={(o) => !o && setShown(null)}
        title={shown ? `${shown.name} is ready` : ""}
        description={`Anything sent here lands in ${ns} and runs its pipeline.`}
        actions={
          <Button variant="primary" onClick={() => setShown(null)}>
            Done
          </Button>
        }
      >
        {shown && <HookTokenReveal token={shown.token} />}
      </Dialog>
      <Dialog
        open={Boolean(rekeying)}
        onOpenChange={(o) => !o && setRekeying(null)}
        title="Make a new token?"
        description="Whatever pushes with the old one stops getting through until you give it the new one."
        actions={
          <>
            <Button variant="ghost" onClick={() => setRekeying(null)}>
              Cancel
            </Button>
            <Button variant="primary" disabled={rekey.isPending} onClick={() => rekeying && rekey.mutate(rekeying)}>
              {rekey.isPending ? "Making…" : "Make new token"}
            </Button>
          </>
        }
      />
      <Dialog
        open={Boolean(deleting)}
        onOpenChange={(o) => !o && setDeleting(null)}
        title={`Delete ${deleting?.name ?? "this webhook"}?`}
        description="Its token stops working. What it already imported stays."
        actions={
          <>
            <Button variant="ghost" onClick={() => setDeleting(null)}>
              Cancel
            </Button>
            <Button variant="danger" disabled={remove.isPending} onClick={() => deleting && remove.mutate(deleting)}>
              {remove.isPending ? "Deleting…" : "Delete"}
            </Button>
          </>
        }
      />
    </section>
  );
}

/** A hook's address and token, shown once after it's made or re-keyed. */
function HookTokenReveal({ token }: { token: string }) {
  const base = origin();
  const ex = hookExamples(base, token);
  return (
    <div className="flex flex-col gap-3">
      <Banner tone="warning">Copy the token now: it isn’t shown again. You can make a new one later.</Banner>
      <Field label="Push to" hint="POST, with the token as a bearer token">
        {() => <CodeBlock text={`${base}${PUSH}`} label="address" />}
      </Field>
      <Field label="Token">{() => <CodeBlock text={token} label="token" />}</Field>
      <Field label="Send a file" hint="Audio, video, documents, images and subtitles; or as form files">
        {() => <CodeBlock text={ex.file} label="file example" />}
      </Field>
      <Field label="Or a link" hint='A PDF or web page; {"text": "..."} imports text'>
        {() => <CodeBlock text={ex.link} label="link example" />}
      </Field>
    </div>
  );
}

function NewHookDialog({
  ns,
  open,
  onClose,
  onMade,
}: {
  ns: string;
  open: boolean;
  onClose: () => void;
  onMade: (name: string, token: string) => void;
}) {
  const client = useApiClient();
  const [name, setName] = useState("");
  const make = useMutation({
    mutationFn: (n: string) => data(ImportHooks.createImportHook({ client, path: { name: ns }, body: { name: n } })),
    onSuccess: (r) => onMade(r.hook.name, r.token),
  });
  const { reset } = make;
  useEffect(() => {
    if (open) {
      setName("");
      reset();
    }
  }, [open, reset]);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (name.trim()) make.mutate(name.trim());
  };
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => !o && onClose()}
      title="New import webhook"
      description={`It gets its own token, and what it sends lands in ${ns}.`}
    >
      <form onSubmit={submit} className="flex flex-col gap-4">
        <Field label="Name" hint="What will push to it, e.g. Office scanner or Zapier">
          {(f) => (
            <Input
              id={f.id}
              aria-describedby={f.describedBy}
              value={name}
              maxLength={80}
              autoFocus
              onChange={(e) => setName(e.target.value)}
            />
          )}
        </Field>
        {make.isError && <Banner tone="error">{make.error.message}</Banner>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="submit"
            variant="primary"
            icon={<KeyRound />}
            disabled={!name.trim() || make.isPending}
            disabledReason={!name.trim() ? "Give it a name" : undefined}
          >
            {make.isPending ? "Making…" : "Make webhook"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
