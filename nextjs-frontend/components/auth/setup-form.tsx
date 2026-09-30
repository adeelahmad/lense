"use client";

import { useState } from "react";

import { setup } from "@/components/actions/setup-action";
import { AuthAlert, AuthCard } from "@/components/auth/auth-card";
import { AuthField } from "@/components/auth/auth-field";
import { AuthSubmit } from "@/components/auth/auth-submit";
import { useFormAction } from "@/components/auth/use-form-action";
import { PASSWORD_MIN_LENGTH, passwordShortBy } from "@/lib/definitions";

const EMAIL = /^\S+@\S+\.\S+$/;

/** First-run setup with the one-time code from the server log (Access AC1). Shown only while no accounts exist. */
export function SetupForm() {
  const { state, pending, onSubmit, action } = useFormAction(setup);
  const [code, setCode] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  // A server error for a field stays until that field changes.
  const [sent, setSent] = useState<{ code: string; email: string; password: string } | null>(null);

  const ready = code.trim().length > 0 && EMAIL.test(email.trim()) && [...password].length >= PASSWORD_MIN_LENGTH;
  const current = { code, email, password };
  const fieldState = (field: keyof typeof current) => (sent && sent[field] !== current[field] ? undefined : state);
  const formError = state?.server_validation_error || state?.server_error;

  return (
    <AuthCard wide title="Set up this server" description="No accounts exist yet. The account you create here is the platform admin.">
      <div className="flex flex-col gap-1.5">
        <span className="text-[12.5px] leading-[1.4] text-fg-secondary">Find the setup code in the server log:</span>
        <code className="block overflow-x-auto whitespace-pre rounded-[10px] bg-term-bg px-3 py-2.5 font-mono text-[12px] leading-normal text-term-fg">
          {'$ docker logs lens 2>&1 | grep "setup code"\n'}
          <span className="text-[var(--term-gold)]">… with setup code: &lt;one-time code&gt;</span>
        </code>
        <span className="text-[12px] leading-[1.4] text-fg-muted">
          Or start the server with <code className="font-mono text-[11.5px] text-fg-strong">LENS_SETUP_CODE</code> set to choose it yourself.
        </span>
      </div>
      <form
        action={action}
        onSubmit={(e) => {
          setSent({ code, email, password });
          onSubmit(e);
        }}
        className="flex flex-col gap-4"
        noValidate
      >
        <AuthField name="code" label="Setup code" mono autoComplete="off" spellCheck={false} required value={code} onChange={(e) => setCode(e.target.value)} state={fieldState("code")} />
        <AuthField name="name" label="Name" autoComplete="name" state={state} />
        <AuthField name="email" label="Email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} state={fieldState("email")} />
        <AuthField
          name="password"
          label="Password"
          type="password"
          autoComplete="new-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          hint={`At least ${PASSWORD_MIN_LENGTH} characters`}
          error={passwordShortBy(password)}
          state={fieldState("password")}
        />
        {formError && <AuthAlert tone="error">{formError}</AuthAlert>}
        <AuthSubmit pending={pending} pendingText="Creating…" disabled={!ready} disabledReason="Fill in the setup code, your email and a password of at least 10 characters">
          Create admin account
        </AuthSubmit>
      </form>
    </AuthCard>
  );
}
