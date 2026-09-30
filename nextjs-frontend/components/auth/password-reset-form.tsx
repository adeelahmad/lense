"use client";

import Link from "next/link";

import { passwordReset } from "@/components/actions/password-reset-action";
import { AuthAlert, AuthCard } from "@/components/auth/auth-card";
import { AuthField } from "@/components/auth/auth-field";
import { AuthSubmit } from "@/components/auth/auth-submit";
import { useFormAction } from "@/components/auth/use-form-action";

/** Ask for a reset link by email. If this server can't send email, an admin resets the password instead. */
export function PasswordResetForm() {
  const { state, pending, onSubmit, action } = useFormAction(passwordReset);
  const error = state?.server_validation_error || state?.server_error;

  return (
    <AuthCard
      title="Reset your password"
      description="Enter your email and we'll send a link to choose a new password."
      footer={
        <>
          No email? Ask an admin to reset it. ·{" "}
          <Link href="/login" className="text-fg-secondary underline underline-offset-2 hover:text-fg">
            Back to sign in
          </Link>
        </>
      }
    >
      {error && <AuthAlert tone="error">{error}</AuthAlert>}
      {state?.message && <AuthAlert tone="success">{state.message}</AuthAlert>}
      <form action={action} onSubmit={onSubmit} className="flex flex-col gap-4" noValidate>
        <AuthField name="email" label="Email" type="email" autoComplete="email" required state={state} />
        <AuthSubmit pending={pending} pendingText="Sending…">
          Send reset link
        </AuthSubmit>
      </form>
    </AuthCard>
  );
}
