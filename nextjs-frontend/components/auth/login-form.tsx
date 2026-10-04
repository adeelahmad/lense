"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import type { ExternalProvider } from "@/app/openapi-client";
import { login } from "@/components/actions/login-action";
import { AuthAlert, AuthCard } from "@/components/auth/auth-card";
import { AuthField } from "@/components/auth/auth-field";
import { AuthSubmit } from "@/components/auth/auth-submit";
import { ExternalSignIn } from "@/components/auth/external-sign-in";
import { PasskeySignIn } from "@/components/auth/passkey-button";
import { useFormAction } from "@/components/auth/use-form-action";

/** After "too many attempts" the button waits this long before it can be tried again. */
const COOL_DOWN_MS = 60_000;

/**
 * Sign in with a passkey or an outside account an admin set up (Google, GitHub, ...); with a password too where an
 * admin left passwords on (wrong email or password, too many attempts: Access AC2).
 */
export function LoginForm({
  callbackUrl = "/",
  setupRequired = false,
  notice,
  passwords = false,
  providers = [],
}: {
  callbackUrl?: string;
  setupRequired?: boolean;
  notice?: string;
  /** Whether passwords sign in here (auth.passwords). */
  passwords?: boolean;
  /** Outside accounts people can sign in with (Google, GitHub, ...). */
  providers?: ExternalProvider[];
}) {
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
        passwords ? (
          <>
            Forgot your password? Ask an admin to reset it, or{" "}
            <Link href="/password-recovery" className="text-fg-secondary underline underline-offset-2 hover:text-fg">
              get a reset link by email
            </Link>
            .
          </>
        ) : (
          <>
            No passkey on this device? Your browser can use one on your phone. Lost it? Ask an admin for a sign-in link,
            or{" "}
            <Link href="/password-recovery" className="text-fg-secondary underline underline-offset-2 hover:text-fg">
              get one by email
            </Link>
            .
          </>
        )
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
      <PasskeySignIn
        callbackUrl={callbackUrl}
        autoFocus={!setupRequired}
        hideIfUnavailable={passwords || providers.length > 0}
      />
      <ExternalSignIn providers={providers} callbackUrl={callbackUrl} />
      {passwords && (
        <>
          <div className="flex items-center gap-3 text-[12px] text-fg-muted" aria-hidden>
            <span className="h-px flex-1 bg-border" />
            or with your password
            <span className="h-px flex-1 bg-border" />
          </div>
          {error && <AuthAlert tone={state?.throttled ? "gate" : "error"}>{error}</AuthAlert>}
          <form action={action} onSubmit={onSubmit} className="flex flex-col gap-4" noValidate>
            <input type="hidden" name="callbackUrl" value={callbackUrl} />
            <AuthField name="email" label="Email" type="email" autoComplete="email" required state={state} />
            <AuthField
              name="password"
              label="Password"
              type="password"
              autoComplete="current-password"
              required
              state={state}
            />
            <AuthSubmit pending={pending} pendingText="Signing in…" disabled={coolingDown}>
              {coolingDown ? "Try again in a few minutes" : "Sign in with password"}
            </AuthSubmit>
          </form>
        </>
      )}
    </AuthCard>
  );
}
