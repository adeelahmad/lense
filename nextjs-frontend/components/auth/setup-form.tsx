"use client";

import { useState } from "react";

import { setup } from "@/components/actions/setup-action";
import { AuthAlert, AuthCard } from "@/components/auth/auth-card";
import { AuthField } from "@/components/auth/auth-field";
import { AuthSubmit } from "@/components/auth/auth-submit";
import { CodeBlock } from "@/components/ui/states";
import { useFormAction } from "@/components/auth/use-form-action";
import { PASSWORD_MIN_LENGTH, passwordShortBy } from "@/lib/definitions";

const EMAIL = /^\S+@\S+\.\S+$/;

/**
 * First-run setup with the one-time code from the server log (Access AC1). Shown only while no accounts exist. The
 * setup link in the log brings the code along, so then there is nothing to look up.
 */
export function SetupForm({ initialCode = "" }: { initialCode?: string }) {
  const { state, pending, onSubmit, action } = useFormAction(setup);
  const [code, setCode] = useState(initialCode);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  // A server error for a field stays until that field changes.
  const [sent, setSent] = useState<{
    code: string;
    email: string;
    password: string;
  } | null>(null);

  const ready = code.trim().length > 0 && EMAIL.test(email.trim()) && [...password].length >= PASSWORD_MIN_LENGTH;
  const current = { code, email, password };
  const fieldState = (field: keyof typeof current) => (sent && sent[field] !== current[field] ? undefined : state);
  const formError = state?.server_validation_error || state?.server_error;

  return (
    <AuthCard
      wide
      title="Set up this server"
      description="No accounts exist yet. The account you create here is the platform admin."
    >
      {initialCode ? (
        <AuthAlert tone="success">The setup code from your link is filled in.</AuthAlert>
      ) : (
        <div className="flex flex-col gap-1.5">
          <span className="text-[12.5px] leading-[1.4] text-fg-secondary">
            The server log has the setup code and a link that fills it in. From the Lens folder:
          </span>
          <CodeBlock text="make setup-code" label="the command" />
          <span className="text-[12px] leading-[1.4] text-fg-muted">
            Or start the server with <code className="font-mono text-[11.5px] text-fg-strong">LENS_SETUP_CODE</code> set
            to choose it yourself.
          </span>
        </div>
      )}
      <form
        action={action}
        onSubmit={(e) => {
          setSent({ code, email, password });
          onSubmit(e);
        }}
        className="flex flex-col gap-4"
        noValidate
      >
        <AuthField
          name="code"
          label="Setup code"
          mono
          autoComplete="off"
          spellCheck={false}
          required
          autoFocus={!initialCode}
          value={code}
          onChange={(e) => setCode(e.target.value)}
          state={fieldState("code")}
        />
        <AuthField name="name" label="Name" autoComplete="name" autoFocus={Boolean(initialCode)} state={state} />
        <AuthField
          name="email"
          label="Email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          state={fieldState("email")}
        />
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
        <AuthSubmit
          pending={pending}
          pendingText="Creating…"
          disabled={!ready}
          disabledReason="Fill in the setup code, your email and a password of at least 10 characters"
        >
          Create admin account
        </AuthSubmit>
      </form>
    </AuthCard>
  );
}
