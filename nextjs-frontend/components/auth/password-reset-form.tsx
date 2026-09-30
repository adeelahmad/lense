"use client";

import Link from "next/link";
import { useActionState } from "react";

import { passwordReset } from "@/components/actions/password-reset-action";
import { AuthCard } from "@/components/auth/auth-card";
import { FormError } from "@/components/ui/FormError";
import { FormField } from "@/components/ui/form-field";
import { SubmitButton } from "@/components/ui/submitButton";

export function PasswordResetForm() {
  const [state, dispatch] = useActionState(passwordReset, undefined);

  return (
    <AuthCard
      title="Reset your password"
      description="Enter your email and we'll send you a link to choose a new password."
      footer={
        <Link href="/login" className="underline-offset-4 hover:underline">
          Back to sign in
        </Link>
      }
    >
      <form action={dispatch} className="grid gap-4" noValidate>
        <FormField
          name="email"
          label="Email"
          type="email"
          autoComplete="email"
          required
          state={state}
        />
        <FormError state={state} />
        {state?.message && (
          <p role="status" className="text-sm text-muted-foreground">
            {state.message}
          </p>
        )}
        <SubmitButton text="Send reset link" pendingText="Sending…" />
      </form>
    </AuthCard>
  );
}
