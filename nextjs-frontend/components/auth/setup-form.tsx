"use client";

import { useActionState } from "react";

import { setup } from "@/components/actions/setup-action";
import { AuthCard } from "@/components/auth/auth-card";
import { FormError } from "@/components/ui/FormError";
import { FormField } from "@/components/ui/form-field";
import { SubmitButton } from "@/components/ui/submitButton";
import { PASSWORD_MIN_LENGTH } from "@/lib/definitions";

export function SetupForm() {
  const [state, dispatch] = useActionState(setup, undefined);

  return (
    <AuthCard
      title="Set up Lens"
      description="Create the first administrator. The setup code is printed in the server log."
    >
      <form action={dispatch} className="grid gap-4" noValidate>
        <FormField
          name="code"
          label="Setup code"
          autoComplete="off"
          required
          state={state}
        />
        <FormField name="name" label="Name" autoComplete="name" state={state} />
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
        <SubmitButton text="Create administrator" pendingText="Creating…" />
      </form>
    </AuthCard>
  );
}
