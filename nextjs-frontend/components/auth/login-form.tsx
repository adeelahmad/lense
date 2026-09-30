"use client";

import Link from "next/link";
import { useActionState } from "react";

import { login } from "@/components/actions/login-action";
import { AuthCard } from "@/components/auth/auth-card";
import { FormError } from "@/components/ui/FormError";
import { FormField } from "@/components/ui/form-field";
import { SubmitButton } from "@/components/ui/submitButton";

export function LoginForm({
  callbackUrl = "/",
  setupRequired = false,
  notice,
}: {
  callbackUrl?: string;
  setupRequired?: boolean;
  notice?: string;
}) {
  const [state, dispatch] = useActionState(login, undefined);

  return (
    <AuthCard
      title="Sign in"
      description="Accounts are created by an administrator."
      footer={
        setupRequired && (
          <>
            First time here?{" "}
            <Link
              href="/setup"
              className="font-medium text-foreground underline underline-offset-4"
            >
              Set up this archive
            </Link>
          </>
        )
      }
    >
      {notice && (
        <p role="status" className="rounded-md bg-muted px-3 py-2 text-sm">
          {notice}
        </p>
      )}
      <form action={dispatch} className="grid gap-4" noValidate>
        <input type="hidden" name="callbackUrl" value={callbackUrl} />
        <FormField
          name="email"
          label="Email"
          type="email"
          autoComplete="email"
          required
          state={state}
        />
        <FormField
          name="password"
          label="Password"
          type="password"
          autoComplete="current-password"
          required
          state={state}
          hint={
            <Link
              href="/password-recovery"
              className="ml-auto text-sm text-muted-foreground underline-offset-4 hover:underline"
            >
              Forgot your password?
            </Link>
          }
        />
        <FormError state={state} />
        <SubmitButton text="Sign in" pendingText="Signing in…" />
      </form>
    </AuthCard>
  );
}
