"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { login } from "@/components/actions/login-action";
import { AuthAlert, AuthCard } from "@/components/auth/auth-card";
import { AuthField } from "@/components/auth/auth-field";
import { AuthSubmit } from "@/components/auth/auth-submit";
import { useFormAction } from "@/components/auth/use-form-action";

/** After "too many attempts" the button waits this long before it can be tried again. */
const COOL_DOWN_MS = 60_000;

/** Sign in · wrong email or password · too many attempts (Access AC2). */
export function LoginForm({ callbackUrl = "/", setupRequired = false, notice }: { callbackUrl?: string; setupRequired?: boolean; notice?: string }) {
  const { state, pending, onSubmit, action } = useFormAction(login);
  const [coolingDown, setCoolingDown] = useState(false);

  useEffect(() => {
    if (!state?.throttled) return;
    setCoolingDown(true);
    const t = setTimeout(() => setCoolingDown(false), COOL_DOWN_MS);
    return () => clearTimeout(t);
  }, [state]);

  const error = state?.server_validation_error || state?.server_error;
  return (
    <AuthCard
      title="Sign in"
      footer={
        <>
          Forgot your password? Ask an admin to reset it, or{" "}
          <Link href="/password-recovery" className="text-fg-secondary underline underline-offset-2 hover:text-fg">
            get a reset link by email
          </Link>
          .
        </>
      }
    >
      {setupRequired && (
        <AuthAlert tone="info">
          No accounts exist yet.{" "}
          <Link href="/setup" className="font-bold text-fg-accent underline underline-offset-2">
            Set up this archive
          </Link>
        </AuthAlert>
      )}
      {notice && <AuthAlert tone="success">{notice}</AuthAlert>}
      {error && <AuthAlert tone={state?.throttled ? "gate" : "error"}>{error}</AuthAlert>}
      <form action={action} onSubmit={onSubmit} className="flex flex-col gap-4" noValidate>
        <input type="hidden" name="callbackUrl" value={callbackUrl} />
        <AuthField name="email" label="Email" type="email" autoComplete="email" required state={state} />
        <AuthField name="password" label="Password" type="password" autoComplete="current-password" required state={state} />
        <AuthSubmit pending={pending} pendingText="Signing in…" disabled={coolingDown}>
          {coolingDown ? "Try again in a few minutes" : "Sign in"}
        </AuthSubmit>
      </form>
    </AuthCard>
  );
}
