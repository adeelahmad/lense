"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, KeyRound, Plus } from "lucide-react";
import { useState } from "react";

import { Tokens } from "@/app/openapi-client";
import type { ApiToken } from "@/app/openapi-client/types.gen";
import { daysError, expiresOn, SCOPE_LABEL, tokenExpiry } from "@/components/account/token-model";
import { isUnreachable } from "@/components/errors/error-states";
import { ChoiceCards } from "@/components/settings/controls";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input } from "@/components/ui/field";
import { EmptyState, SkeletonRows } from "@/components/ui/states";
import { Table, Td, Th, THead, Tr } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { relative, shortDate } from "@/lib/format";
import { cn } from "@/lib/utils";

const SCOPES = [
  { value: "read", label: "Read only", hint: "read, search, chat" },
  { value: "write", label: "Read & write", hint: "+ import, edit, reprocess" },
];

type Created = { token: string; name: string; scope: string; days: number };

/** API tokens: list · create · shown once · revoke · expired (Access AC5). Each person manages their own. */
export function TokensPage() {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const tokens = useQuery({
    queryKey: ["tokens"],
    queryFn: () => data(Tokens.listTokens({ client })),
  });
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<Created | null>(null);
  const [revoking, setRevoking] = useState<ApiToken | null>(null);

  const list = [...(tokens.data ?? [])].sort((a, b) => (b.created_at || "").localeCompare(a.created_at || ""));

  return (
    <div className="px-4 py-5 sm:px-6">
      <section className="mx-auto flex max-w-[1000px] flex-col gap-3.5 rounded-md border border-border bg-background px-4 py-5 sm:px-6">
        <div className="flex flex-wrap items-start gap-2.5">
          <div className="flex min-w-0 flex-1 flex-col gap-1">
            <h1 className="text-[22px] font-bold leading-[1.2] text-fg">API tokens</h1>
            <p className="text-[13.5px] leading-[1.4] text-fg-secondary">
              Tokens act as you, with your namespace roles. Read only tokens can’t import, edit or reprocess.
            </p>
          </div>
          <Button variant="primary" size="sm" icon={<Plus />} onClick={() => setCreating(true)}>
            New token
          </Button>
        </div>
        {tokens.isPending ? (
          <SkeletonRows rows={3} />
        ) : tokens.isError ? (
          <EmptyState
            tone="error"
            icon={<KeyRound />}
            title={isUnreachable(tokens.error) ? "Can’t reach the server" : "Couldn’t load your tokens"}
            actions={<Button onClick={() => tokens.refetch()}>Try again</Button>}
          >
            {tokens.error.message}
          </EmptyState>
        ) : !list.length ? (
          <EmptyState
            icon={<KeyRound />}
            title="No API tokens yet"
            actions={
              <Button variant="primary" size="sm" icon={<Plus />} onClick={() => setCreating(true)}>
                New token
              </Button>
            }
          >
            A token lets a script or another app (a sync plugin, a CI job) use the archive as you.
          </EmptyState>
        ) : (
          <div className="overflow-hidden rounded-md border border-border">
            <Table aria-label="Your API tokens" className="text-[13px]">
              <THead className="border-t-0">
                <tr>
                  <Th>Name</Th>
                  <Th>Scope</Th>
                  <Th>Prefix</Th>
                  <Th>Created</Th>
                  <Th>Expires</Th>
                  <Th>Last used</Th>
                  <Th>
                    <span className="sr-only">Actions</span>
                  </Th>
                </tr>
              </THead>
              <tbody>
                {list.map((t) => {
                  const exp = tokenExpiry(t.expires_at);
                  const expired = exp.state === "expired";
                  return (
                    <Tr key={t.id} className={cn("h-12 last:border-b-0", expired && "text-fg-muted")}>
                      <Td className="font-semibold">{t.name}</Td>
                      <Td>
                        <span className="inline-flex h-[22px] items-center whitespace-nowrap rounded-pill border border-border px-2 text-[11.5px] font-semibold">
                          {SCOPE_LABEL[t.scope] ?? t.scope}
                        </span>
                      </Td>
                      <Td>
                        <code className="font-mono text-[12px] font-medium">{t.prefix}…</code>
                      </Td>
                      <Td className="tabular whitespace-nowrap">{shortDate(t.created_at)}</Td>
                      <Td
                        className={cn(
                          "tabular whitespace-nowrap",
                          exp.state === "soon" ? "text-gold-dark" : !expired && "text-fg-secondary",
                        )}
                      >
                        {exp.label}
                      </Td>
                      <Td className="tabular whitespace-nowrap text-fg-secondary">
                        {t.last_used_at ? relative(t.last_used_at) : "never"}
                      </Td>
                      <Td className="text-right">
                        <Button
                          variant={expired ? "ghost" : "danger-ghost"}
                          size="xs"
                          onClick={() => setRevoking(t)}
                          aria-label={`${expired ? "Delete" : "Revoke"} ${t.name}`}
                        >
                          {expired ? "Delete" : "Revoke"}
                        </Button>
                      </Td>
                    </Tr>
                  );
                })}
              </tbody>
            </Table>
          </div>
        )}
      </section>
      <NewTokenDialog
        open={creating}
        onClose={() => setCreating(false)}
        onCreated={(c) => {
          setCreating(false);
          setCreated(c);
          void qc.invalidateQueries({ queryKey: ["tokens"] });
        }}
      />
      <ShownOnceDialog created={created} onClose={() => setCreated(null)} />
      <RevokeDialog
        token={revoking}
        onClose={() => setRevoking(null)}
        onDone={(t, expired) => {
          setRevoking(null);
          void qc.invalidateQueries({ queryKey: ["tokens"] });
          toast({
            title: expired ? `Deleted “${t.name}”` : `Revoked “${t.name}”`,
            body: expired ? undefined : `Anything using ${t.prefix}… stops working now.`,
            tone: "green",
          });
        }}
      />
    </div>
  );
}

function NewTokenDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (c: Created) => void;
}) {
  const client = useApiClient();
  const [name, setName] = useState("");
  const [scope, setScope] = useState("read");
  const [days, setDays] = useState("");
  const limitsQ = useQuery({
    queryKey: ["tokens", "limits"],
    queryFn: () => data(Tokens.tokenLimits({ client })),
    staleTime: 0, // admins change them
  });
  const limits = limitsQ.data ?? null;
  const shown = days || (limits ? String(limits.default_days) : "");
  const create = useMutation({
    mutationFn: () =>
      data(
        Tokens.createToken({
          client,
          body: {
            name: name.trim(),
            scope: scope as "read" | "write",
            days: Number(shown),
          },
        }),
      ),
    onSuccess: (r) => {
      onCreated({
        token: r.token,
        name: name.trim(),
        scope,
        days: Number(shown),
      });
      setName("");
      setScope("read");
      setDays("");
    },
  });
  const nameError = name.trim() ? (name.trim().length > 80 ? "Use at most 80 characters" : null) : null;
  const dError = limits ? daysError(shown, limits) : "Loading how long keys may last…";
  const ready = name.trim() && !nameError && !dError;

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) {
          create.reset();
          onClose();
        }
      }}
      title="New API token"
      actions={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={!ready || create.isPending}
            disabledReason={!ready ? "Give the token a name and a number of days" : undefined}
            onClick={() => create.mutate()}
          >
            {create.isPending ? "Creating…" : "Create token"}
          </Button>
        </>
      }
    >
      <form
        method="post"
        className="flex flex-col gap-3.5"
        onSubmit={(e) => {
          e.preventDefault();
          if (ready) create.mutate();
        }}
      >
        <Field label="Name" hint="So you can recognise it later" error={nameError}>
          {(f) => (
            <Input
              id={f.id}
              aria-describedby={f.describedBy}
              invalid={f.invalid}
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoFocus
              maxLength={120}
            />
          )}
        </Field>
        <div className="flex flex-col gap-2">
          <span className="text-[13px] font-bold leading-tight text-fg-strong">Scope</span>
          <ChoiceCards label="Scope" options={SCOPES} value={scope} onChange={setScope} />
        </div>
        <Field
          label="Expires after (days)"
          hint={
            dError || !limits
              ? undefined
              : `${expiresOn(Number(shown))} · at most ${limits.max_days} days${limits.never_expire ? ", or 0 for never" : ""}`
          }
          error={shown.trim() && limits ? dError : null}
        >
          {(f) => (
            <Input
              id={f.id}
              aria-describedby={f.describedBy}
              invalid={f.invalid}
              inputMode="numeric"
              value={shown}
              onChange={(e) => setDays(e.target.value)}
            />
          )}
        </Field>
        {create.isError && (
          <Banner tone="error">
            {create.error instanceof ApiError && create.error.status === 403
              ? "Tokens can only be created while signed in with your password."
              : create.error.message}
          </Banner>
        )}
        <button type="submit" hidden />
      </form>
    </Dialog>
  );
}

function ShownOnceDialog({ created, onClose }: { created: Created | null; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    if (!created) return;
    try {
      await navigator.clipboard.writeText(created.token);
      setCopied(true);
    } catch {
      /* clipboard blocked: the token stays selectable */
    }
  };
  return (
    <Dialog
      open={Boolean(created)}
      onOpenChange={(o) => {
        if (!o) {
          setCopied(false);
          onClose();
        }
      }}
      title="Copy your token now"
      description="This is the only time it’s shown. Store it somewhere safe; if you lose it, revoke it and create a new one."
      actions={
        <Button
          variant="primary"
          onClick={() => {
            setCopied(false);
            onClose();
          }}
        >
          I’ve saved it
        </Button>
      }
    >
      {created && (
        <>
          <div className="flex items-center gap-2 rounded-[10px] bg-term-bg py-2.5 pl-3.5 pr-2 text-term-fg">
            <code className="min-w-0 flex-1 select-all break-all font-mono text-[13px] font-medium leading-[1.4]">
              {created.token}
            </code>
            <button
              type="button"
              onClick={copy}
              className={cn(
                "inline-flex h-8 shrink-0 items-center gap-1.5 rounded-pill px-3 text-[12.5px] font-bold",
                copied ? "bg-green text-white" : "text-[var(--term-blue)] hover:bg-white/10",
              )}
            >
              {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
          <p className="text-[12.5px] leading-[1.4] text-fg-muted">
            {created.name} · {SCOPE_LABEL[created.scope]} ·{" "}
            {expiresOn(created.days).replace(/^./, (c) => c.toLowerCase())}
          </p>
        </>
      )}
    </Dialog>
  );
}

function RevokeDialog({
  token,
  onClose,
  onDone,
}: {
  token: ApiToken | null;
  onClose: () => void;
  onDone: (t: ApiToken, expired: boolean) => void;
}) {
  const client = useApiClient();
  const expired = token ? tokenExpiry(token.expires_at).state === "expired" : false;
  const revoke = useMutation({
    mutationFn: (t: ApiToken) => data(Tokens.revokeToken({ client, path: { token_id: t.id } })),
    onSuccess: (_r, t) => onDone(t, expired),
  });
  return (
    <Dialog
      open={Boolean(token)}
      onOpenChange={(o) => {
        if (!o) {
          revoke.reset();
          onClose();
        }
      }}
      title={token ? `${expired ? "Delete" : "Revoke"} “${token.name}”?` : ""}
      actions={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="danger" disabled={revoke.isPending} onClick={() => token && revoke.mutate(token)}>
            {revoke.isPending ? "Working…" : expired ? "Delete token" : "Revoke token"}
          </Button>
        </>
      }
    >
      {token && (
        <p className="text-[14px] leading-normal text-fg-secondary">
          {expired ? (
            <>It has already expired; deleting it removes it from this list.</>
          ) : (
            <>
              Anything using <code className="font-mono text-[12.5px] font-medium text-fg">{token.prefix}…</code> stops
              working immediately.{" "}
              {token.last_used_at ? `It was last used ${relative(token.last_used_at)}.` : "It hasn’t been used yet."}
            </>
          )}
        </p>
      )}
      {revoke.isError && <Banner tone="error">{revoke.error.message}</Banner>}
    </Dialog>
  );
}
