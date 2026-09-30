"use client";

import { useActionState } from "react";

import { passwordResetConfirm } from "@/components/actions/password-reset-action";
import { AuthCard } from "@/components/auth/auth-card";
import { FormError } from "@/components/ui/FormError";
import { FormField } from "@/components/ui/form-field";
import { SubmitButton } from "@/components/ui/submitButton";
import { PASSWORD_MIN_LENGTH } from "@/lib/definitions";

export function PasswordResetConfirmForm({ token }: { token: string }) {
  const [state, dispatch] = useActionState(passwordResetConfirm, undefined);

  return (
    <AuthCard title="Choose a new password">
      <form action={dispatch} className="grid gap-4" noValidate>
        <input type="hidden" name="token" value={token} />
        <FormField
          name="password"
          label="New password"
          type="password"
          autoComplete="new-password"
          minLength={PASSWORD_MIN_LENGTH}
          required
          state={state}
        />
        <FormField
          name="passwordConfirm"
          label="Confirm password"
          type="password"
          autoComplete="new-password"
          required
          state={state}
        />
        <FormError state={state} />
        <SubmitButton text="Change password" pendingText="Saving…" />
      </form>
    </AuthCard>
  );
}
