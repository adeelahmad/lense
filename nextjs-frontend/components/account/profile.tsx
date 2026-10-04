"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, LogOut } from "lucide-react";
import Link from "next/link";
import { signOut } from "next-auth/react";
import { useState } from "react";

import { Auth, Tokens } from "@/app/openapi-client";
import { ConnectedAccountsPanel } from "@/components/account/connected-accounts";
import { PasskeysPanel } from "@/components/account/passkeys";
import { confirmMismatch, passwordBlocked } from "@/components/account/profile-model";
import { Badge, RoleChip, type Role } from "@/components/ui/badge";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import { Panel } from "@/components/ui/panel";
import { Avatar, DateTime, PageHeader, Skeleton } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { PASSWORD_MIN_LENGTH, passwordShortBy } from "@/lib/definitions";
import { useArchive } from "@/lib/hooks/session";

/** Profile and sign-in: everyone changes their own name, manages their passkeys, and, where passwords are on, their
 * password with their current one. */
export function ProfilePage() {
  const { me, admin } = useArchive();
  const client = useApiClient();
  const status = useQuery({ queryKey: ["auth-status"], queryFn: () => data(Auth.status({ client })) });
  const tokens = useQuery({
    queryKey: ["tokens"],
    queryFn: () => data(Tokens.listTokens({ client })),
  });

  if (!me)
    return (
      <div className="flex max-w-[760px] flex-col gap-4 px-4 py-5 sm:px-6">
        <Skeleton className="h-7 w-64" />
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );

  const roles = Object.entries(me.roles ?? {}) as [string, Role][];
  return (
    <div className="flex max-w-[760px] flex-col gap-4 px-4 py-5 sm:px-6">
      <PageHeader title="Profile and sign-in" meta={me.user.email} className="mb-1" />
      <Panel title="Profile">
        <div className="flex flex-col gap-4">
          <div className="flex items-center gap-3">
            <Avatar name={me.user.name || me.user.email} size={40} className="border border-border" />
            <div className="min-w-0">
              <div className="flex items-center gap-2 text-[15px] font-bold text-fg">
                {me.user.name || me.user.email}
                {admin && <Badge tone="intent">Platform admin</Badge>}
              </div>
              <div className="text-[13px] text-fg-muted">
                {me.user.email} · signed in <DateTime iso={me.user.last_login_at} />
              </div>
            </div>
          </div>
          <NameForm name={me.user.name ?? ""} />
          <div className="flex flex-col gap-2">
            <span className="text-[13px] font-bold text-fg-strong">Your roles</span>
            {admin ? (
              <p className="text-[13px] text-fg-secondary">As a platform admin you own every namespace.</p>
            ) : roles.length ? (
              <ul className="flex flex-col divide-y divide-border rounded-sm border border-border">
                {roles.map(([ns, role]) => (
                  <li key={ns} className="flex items-center justify-between px-3 py-2 text-[13.5px]">
                    <span className="font-medium text-fg">{ns}</span>
                    <RoleChip role={role} />
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-[13px] text-fg-secondary">
                You have no namespaces yet. An owner or admin can add you.
              </p>
            )}
          </div>
        </div>
      </Panel>
      <PasskeysPanel />
      <ConnectedAccountsPanel />
      {status.data?.passwords && (
        <Panel title="Password">
          <div className="flex flex-col gap-5">
            <ChangePassword />
            <ResetByEmail email={me.user.email} />
          </div>
        </Panel>
      )}
      <Panel
        title="API tokens"
        subtitle="For scripts and other apps that use the archive as you."
        actions={
          <Button asChild size="sm">
            <Link href="/account/tokens">
              <KeyRound /> Manage{tokens.data ? ` (${tokens.data.length})` : ""}
            </Link>
          </Button>
        }
      />
      <div>
        <Button variant="ghost" icon={<LogOut />} onClick={() => void signOut({ redirectTo: "/login" })}>
          Sign out
        </Button>
      </div>
    </div>
  );
}

function NameForm({ name }: { name: string }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const [value, setValue] = useState(name);
  const save = useMutation({
    mutationFn: () => data(Auth.updateMe({ client, body: { name: value.trim() } })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["me"] });
      toast({ title: "Name saved", tone: "green" });
    },
  });
  const changed = value.trim() !== name && value.trim().length > 0;
  return (
    <form
      method="post"
      className="flex flex-col gap-1.5"
      onSubmit={(e) => {
        e.preventDefault();
        if (changed) save.mutate();
      }}
    >
      <Field label="Name" hint="How others see you in the archive">
        {(f) => (
          <div className="flex gap-2">
            <Input
              id={f.id}
              aria-describedby={f.describedBy}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              maxLength={80}
              autoComplete="name"
              className="max-w-[360px]"
            />
            <Button
              type="submit"
              disabled={!changed || save.isPending}
              disabledReason={
                !changed ? (value.trim() ? "Change your name first" : "Your name can’t be empty") : undefined
              }
            >
              {save.isPending ? "Saving…" : "Save"}
            </Button>
          </div>
        )}
      </Field>
      {save.isError && <Banner tone="error">{save.error.message}</Banner>}
    </form>
  );
}

function ChangePassword() {
  const client = useApiClient();
  const toast = useToast();
  const [draft, setDraft] = useState({ current: "", next: "", confirm: "" });
  const [error, setError] = useState<string | null>(null);
  const set = (k: keyof typeof draft) => (e: { target: { value: string } }) => {
    setDraft((d) => ({ ...d, [k]: e.target.value }));
    setError(null);
  };
  const blocked = passwordBlocked(draft);
  const change = useMutation({
    mutationFn: () =>
      data(Auth.changePassword({ client, body: { current_password: draft.current, new_password: draft.next } })),
    onSuccess: () => {
      setDraft({ current: "", next: "", confirm: "" });
      toast({
        title: "Password changed",
        body: "You stay signed in here; your other devices were signed out.",
        tone: "green",
      });
    },
    onError: (e) => setError(e.message),
  });
  return (
    <form
      method="post"
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (!blocked) change.mutate();
      }}
    >
      <p className="text-[13px] leading-normal text-fg-secondary">
        Changing it signs you out on your other devices. Your API tokens keep working.
      </p>
      <Field label="Current password">
        {(f) => (
          <Input
            id={f.id}
            aria-describedby={f.describedBy}
            type="password"
            autoComplete="current-password"
            value={draft.current}
            onChange={set("current")}
            className="sm:max-w-[calc(50%-6px)]"
          />
        )}
      </Field>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field
          label="New password"
          hint={`At least ${PASSWORD_MIN_LENGTH} characters`}
          error={passwordShortBy(draft.next)}
        >
          {(f) => (
            <Input
              id={f.id}
              aria-describedby={f.describedBy}
              invalid={f.invalid}
              type="password"
              autoComplete="new-password"
              value={draft.next}
              onChange={set("next")}
            />
          )}
        </Field>
        <Field label="Confirm new password" error={confirmMismatch(draft)}>
          {(f) => (
            <Input
              id={f.id}
              aria-describedby={f.describedBy}
              invalid={f.invalid}
              type="password"
              autoComplete="new-password"
              value={draft.confirm}
              onChange={set("confirm")}
            />
          )}
        </Field>
      </div>
      {error && <Banner tone="error">{error}</Banner>}
      <div>
        <Button
          type="submit"
          variant="primary"
          disabled={Boolean(blocked) || change.isPending}
          disabledReason={blocked ?? undefined}
        >
          {change.isPending ? "Changing…" : "Change password"}
        </Button>
      </div>
    </form>
  );
}

function ResetByEmail({ email }: { email: string }) {
  const client = useApiClient();
  const send = useMutation({
    mutationFn: () => data(Auth.forgotPassword({ client, body: { email } })),
  });
  return (
    <div className="flex flex-col gap-3">
      <p className="border-t border-border pt-4 text-[13px] leading-normal text-fg-secondary">
        Forgot your current password? Get a reset link sent to <b className="text-fg">{email}</b>. If this server can’t
        send email, ask an admin to reset it for you.
      </p>
      {send.isSuccess ? (
        <Banner tone="success">
          If email is set up on this server, a reset link is on its way. It works for an hour.
        </Banner>
      ) : send.isError ? (
        <Banner tone="error">{send.error.message}</Banner>
      ) : null}
      <div>
        <Button onClick={() => send.mutate()} disabled={send.isPending}>
          {send.isPending ? "Sending…" : send.isSuccess ? "Send again" : "Email me a reset link"}
        </Button>
      </div>
    </div>
  );
}
