"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, LogOut } from "lucide-react";
import Link from "next/link";
import { signIn, signOut } from "next-auth/react";
import { useState } from "react";

import { Auth, Tokens, Users } from "@/app/openapi-client";
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

/**
 * Profile and password. The API has no self-service profile or password change for a signed-in person: admins change
 * their own through the admin account update; everyone else uses the emailed reset link or asks an admin.
 */
export function ProfilePage() {
  const { me, admin } = useArchive();
  const client = useApiClient();
  const tokens = useQuery({ queryKey: ["tokens"], queryFn: () => data(Tokens.listTokens({ client })) });

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
      <PageHeader title="Profile and password" meta={me.user.email} className="mb-1" />
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
          <NameForm id={me.user.id} name={me.user.name ?? ""} canEdit={admin} />
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
              <p className="text-[13px] text-fg-secondary">You have no namespaces yet. An owner or admin can add you.</p>
            )}
          </div>
        </div>
      </Panel>
      <Panel title="Password">{admin ? <ChangePassword id={me.user.id} email={me.user.email} /> : <ResetByEmail email={me.user.email} />}</Panel>
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

function NameForm({ id, name, canEdit }: { id: number; name: string; canEdit: boolean }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const [value, setValue] = useState(name);
  const save = useMutation({
    mutationFn: () => data(Users.updateUser({ client, path: { uid: id }, body: { name: value.trim() } })),
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
        if (canEdit && changed) save.mutate();
      }}
    >
      <Field label="Name" hint={canEdit ? undefined : "Only admins can change names. Ask an admin if yours is wrong."}>
        {(f) => (
          <div className="flex gap-2">
            <Input id={f.id} aria-describedby={f.describedBy} value={value} onChange={(e) => setValue(e.target.value)} disabled={!canEdit} maxLength={80} className="max-w-[360px]" />
            <Button type="submit" disabled={!canEdit || !changed || save.isPending} disabledReason={!canEdit ? "Only admins can change names" : undefined}>
              {save.isPending ? "Saving…" : "Save"}
            </Button>
          </div>
        )}
      </Field>
      {save.isError && <Banner tone="error">{save.error.message}</Banner>}
    </form>
  );
}

function ChangePassword({ id, email }: { id: number; email: string }) {
  const client = useApiClient();
  const toast = useToast();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const short = passwordShortBy(password);
  const mismatch = confirm && confirm !== password ? "Passwords don’t match" : null;
  const ready = password.length >= PASSWORD_MIN_LENGTH && confirm === password;
  const change = useMutation({
    mutationFn: async () => {
      await data(Users.updateUser({ client, path: { uid: id }, body: { password } }));
      // A new password ends every session, this one included: sign straight back in with it.
      const res = await signIn("credentials", { email, password, redirect: false });
      if (!res || res.error) throw new Error("Password changed, but signing back in failed. Sign in again with the new password.");
    },
    onSuccess: () => {
      setPassword("");
      setConfirm("");
      setError(null);
      toast({ title: "Password changed", body: "Your other devices were signed out.", tone: "green" });
    },
    onError: (e) => setError(e.message),
  });
  return (
    <form
      method="post"
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (ready) change.mutate();
      }}
    >
      <p className="text-[13px] leading-normal text-fg-secondary">Changing it signs you out on your other devices. Your API tokens keep working.</p>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="New password" hint={`At least ${PASSWORD_MIN_LENGTH} characters`} error={short}>
          {(f) => <Input id={f.id} aria-describedby={f.describedBy} invalid={f.invalid} type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />}
        </Field>
        <Field label="Confirm new password" error={mismatch}>
          {(f) => <Input id={f.id} aria-describedby={f.describedBy} invalid={f.invalid} type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />}
        </Field>
      </div>
      {error && <Banner tone="error">{error}</Banner>}
      <div>
        <Button type="submit" variant="primary" disabled={!ready || change.isPending} disabledReason={!ready ? "Type the new password twice" : undefined}>
          {change.isPending ? "Changing…" : "Change password"}
        </Button>
      </div>
    </form>
  );
}

function ResetByEmail({ email }: { email: string }) {
  const client = useApiClient();
  const send = useMutation({ mutationFn: () => data(Auth.forgotPassword({ client, body: { email } })) });
  return (
    <div className="flex flex-col gap-3">
      <p className="text-[13px] leading-normal text-fg-secondary">
        To change your password, get a reset link sent to <b className="text-fg">{email}</b>. If this server can’t send email, ask an admin to reset it for you.
      </p>
      {send.isSuccess ? (
        <Banner tone="success">If email is set up on this server, a reset link is on its way. It works for an hour.</Banner>
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
