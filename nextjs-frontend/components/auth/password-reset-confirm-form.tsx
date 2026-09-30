"use client";

import { useState } from "react";

import { passwordResetConfirm } from "@/components/actions/password-reset-action";
import { AuthAlert, AuthCard } from "@/components/auth/auth-card";
import { AuthField } from "@/components/auth/auth-field";
import { AuthSubmit } from "@/components/auth/auth-submit";
import { useFormAction } from "@/components/auth/use-form-action";
import { PASSWORD_MIN_LENGTH, passwordShortBy } from "@/lib/definitions";

/** Choose a new password from the emailed reset link. */
export function PasswordResetConfirmForm({ token }: { token: string }) {
  const { state, pending, onSubmit, action } = useFormAction(passwordResetConfirm);
  const [password, setPassword] = useState("");
  const error = state?.server_validation_error || state?.server_error;

  return (
    <AuthCard title="Choose a new password" description="Saving it signs your account out on every device.">
      {error && <AuthAlert tone="error">{error}</AuthAlert>}
      <form action={action} onSubmit={onSubmit} className="flex flex-col gap-4" noValidate>
        <input type="hidden" name="token" value={token} />
        <AuthField
          name="password"
          label="New password"
          type="password"
          autoComplete="new-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          hint={`At least ${PASSWORD_MIN_LENGTH} characters`}
          error={passwordShortBy(password)}
          state={state}
        />
        <AuthField name="passwordConfirm" label="Confirm password" type="password" autoComplete="new-password" required state={state} />
        <AuthSubmit pending={pending} pendingText="Saving…">
          Change password
        </AuthSubmit>
      </form>
    </AuthCard>
  );
}
