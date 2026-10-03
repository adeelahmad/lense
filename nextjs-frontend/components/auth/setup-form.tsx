"use client";

import { Fingerprint } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { setup } from "@/components/actions/setup-action";
import { AuthAlert, AuthCard } from "@/components/auth/auth-card";
import { AuthField } from "@/components/auth/auth-field";
import { AuthSubmit } from "@/components/auth/auth-submit";
import { finishSignIn, PasskeyFlowError, setupTicket } from "@/components/auth/passkey-flows";
import { useFormAction } from "@/components/auth/use-form-action";
import { Button } from "@/components/ui/button";
import { PASSWORD_MIN_LENGTH, passwordShortBy } from "@/lib/definitions";
import { deviceName, passkeyErrorMessage, passkeysUnavailableReason } from "@/lib/auth/webauthn";

const EMAIL = /^\S+@\S+\.\S+$/;

/**
 * First-run setup with the one-time code from the server log (Access AC1). Shown only while no accounts exist. The
 * first admin signs in with a passkey: no password at all. Where the browser can't use passkeys (plain http:// other
 * than localhost) they get a password instead, which turns passwords on.
 */
export function SetupForm({ initialCode = "" }: { initialCode?: string }) {
  const { state, pending, onSubmit, action } = useFormAction(setup);
  const [code, setCode] = useState(initialCode);
  const [name, setName] = useState("");
  // null until mounted: whether passkeys work here is only known in the browser
  const [unavailable, setUnavailable] = useState<string | null | undefined>(undefined);
  const [usePassword, setUsePassword] = useState(false);
  const [passkeyBusy, setPasskeyBusy] = useState(false);
  const [passkeyError, setPasskeyError] = useState<{ field?: "code" | "email"; text: string } | null>(null);
  useEffect(() => setUnavailable(passkeysUnavailableReason()), []);
  const passwordMode = usePassword || Boolean(unavailable);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  // A server error for a field stays until that field changes.
  const [sent, setSent] = useState<{
    code: string;
    email: string;
    password: string;
  } | null>(null);

  const identified = code.trim().length > 0 && EMAIL.test(email.trim());
  const ready = identified && [...password].length >= PASSWORD_MIN_LENGTH;
  const current = { code, email, password };
  const fieldState = (field: keyof typeof current) => (sent && sent[field] !== current[field] ? undefined : state);
  const formError = passwordMode
    ? state?.server_validation_error || state?.server_error
    : passkeyError?.field
      ? null
      : passkeyError?.text;

  async function withPasskey(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setPasskeyBusy(true);
    setPasskeyError(null);
    try {
      const ticket = await setupTicket(
        { code: code.trim(), email: email.trim(), name: name.trim() || undefined },
        deviceName(),
      );
      await finishSignIn(ticket, "/welcome");
    } catch (err) {
      if (err instanceof PasskeyFlowError && err.status === 403) {
        setPasskeyError({
          field: "code",
          text: "Setup is closed or the code is wrong. Copy the code again from the server log.",
        });
      } else if (err instanceof PasskeyFlowError && /email/i.test(err.message)) {
        setPasskeyError({ field: "email", text: err.message });
      } else {
        setPasskeyError({ text: passkeyErrorMessage(err, true) });
      }
      setPasskeyBusy(false);
    }
  }
  const passkeyField = (field: "code" | "email") => (passkeyError?.field === field ? passkeyError.text : null);

  return (
    <AuthCard
      wide
      title="Set up this server"
      description="No accounts exist yet. The account you create here is the platform admin. It signs in with a passkey: your fingerprint, face or device PIN, no password."
    >
      <div className="flex flex-col gap-1.5">
        <span className="text-[12.5px] leading-[1.4] text-fg-secondary">Find the setup code in the server log:</span>
        <code className="block overflow-x-auto whitespace-pre rounded-[10px] bg-term-bg px-3 py-2.5 font-mono text-[12px] leading-normal text-term-fg">
          {'$ docker logs lens 2>&1 | grep "setup code"\n'}
          <span className="text-[var(--term-gold)]">… with setup code: &lt;one-time code&gt;</span>
        </code>
        <span className="text-[12px] leading-[1.4] text-fg-muted">
          Or start the server with <code className="font-mono text-[11.5px] text-fg-strong">LENS_SETUP_CODE</code> set
          to choose it yourself.
        </span>
      </div>
      <form
        action={action}
        onSubmit={(e) => {
          if (!passwordMode) return void withPasskey(e);
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
          value={code}
          onChange={(e) => {
            setCode(e.target.value);
            setPasskeyError(null);
          }}
          error={passkeyField("code")}
          state={fieldState("code")}
        />
        <AuthField
          name="name"
          label="Name"
          autoComplete="name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          state={state}
        />
        <AuthField
          name="email"
          label="Email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(e) => {
            setEmail(e.target.value);
            setPasskeyError(null);
          }}
          error={passkeyField("email")}
          state={fieldState("email")}
        />
        {passwordMode && (
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
        )}
        {unavailable && (
          <p className="text-[12.5px] leading-snug text-fg-muted">
            {unavailable} You can set up with a password now, add a passkey later at the https:// address, and then turn
            passwords off in Settings.
          </p>
        )}
        {formError && <AuthAlert tone="error">{formError}</AuthAlert>}
        {passwordMode ? (
          <AuthSubmit
            pending={pending}
            pendingText="Creating…"
            disabled={!ready}
            disabledReason="Fill in the setup code, your email and a password of at least 10 characters"
          >
            Create admin account
          </AuthSubmit>
        ) : (
          <Button
            type="submit"
            variant="primary"
            size="lg"
            className="w-full"
            icon={<Fingerprint />}
            disabled={!identified || passkeyBusy || unavailable === undefined}
            disabledReason={!identified ? "Fill in the setup code and your email" : undefined}
          >
            {passkeyBusy ? "Waiting for your passkey…" : "Create admin with a passkey"}
          </Button>
        )}
        {!unavailable && unavailable !== undefined && (
          <button
            type="button"
            className="self-center text-[12.5px] text-fg-secondary underline underline-offset-2 hover:text-fg"
            onClick={() => setUsePassword((v) => !v)}
          >
            {usePassword ? "Use a passkey instead (no password)" : "Use a password instead"}
          </button>
        )}
      </form>
    </AuthCard>
  );
}
