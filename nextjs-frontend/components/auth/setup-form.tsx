"use client";

import { Fingerprint } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { AuthAlert, AuthCard } from "@/components/auth/auth-card";
import { AuthField } from "@/components/auth/auth-field";
import {
  finishSignIn,
  PasskeyFlowError,
  setupTicket,
  setupWithoutPasskeyTicket,
} from "@/components/auth/passkey-flows";
import { Button } from "@/components/ui/button";
import { CodeBlock } from "@/components/ui/states";
import { deviceName, passkeyErrorMessage, passkeysUnavailableReason } from "@/lib/auth/webauthn";

const EMAIL = /^\S+@\S+\.\S+$/;

/**
 * First-run setup with the one-time code from the server log (Access AC1). Shown only while no accounts exist; the
 * setup link in the log brings the code along, so then there is nothing to look up. The first admin signs in with a
 * passkey: no password at all. Where the browser can't make passkeys (a plain http:// address other than localhost)
 * the code alone makes the admin; they sign in later with a passkey at the https:// address, or with a one-time
 * sign-in link.
 */
export function SetupForm({ initialCode = "" }: { initialCode?: string }) {
  const [code, setCode] = useState(initialCode);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  // undefined until mounted: whether passkeys work here is only known in the browser
  const [unavailable, setUnavailable] = useState<string | null | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ field?: "code" | "email"; text: string } | null>(null);
  useEffect(() => setUnavailable(passkeysUnavailableReason()), []);

  const ready = code.trim().length > 0 && EMAIL.test(email.trim());
  const fieldError = (field: "code" | "email") => (error?.field === field ? error.text : null);

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!ready || busy) return;
    setBusy(true);
    setError(null);
    const body = { code: code.trim(), email: email.trim(), name: name.trim() || undefined };
    try {
      const ticket = unavailable ? await setupWithoutPasskeyTicket(body) : await setupTicket(body, deviceName());
      await finishSignIn(ticket, "/welcome");
    } catch (err) {
      if (err instanceof PasskeyFlowError && err.status === 403) {
        setError({
          field: "code",
          text: "Setup is closed or the code is wrong. Copy the code again from the server log.",
        });
      } else if (err instanceof PasskeyFlowError && /email/i.test(err.message)) {
        setError({ field: "email", text: err.message });
      } else {
        setError({ text: passkeyErrorMessage(err, true) });
      }
      setBusy(false);
    }
  }

  return (
    <AuthCard
      wide
      title="Set up this server"
      description="No accounts exist yet. The account you create here is the platform admin. It signs in with a passkey: your fingerprint, face or device PIN, no password."
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
      <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
        <AuthField
          name="code"
          label="Setup code"
          mono
          autoComplete="off"
          spellCheck={false}
          required
          autoFocus={!initialCode}
          value={code}
          onChange={(e) => {
            setCode(e.target.value);
            setError(null);
          }}
          error={fieldError("code")}
        />
        <AuthField
          name="name"
          label="Name"
          autoComplete="name"
          autoFocus={Boolean(initialCode)}
          value={name}
          onChange={(e) => setName(e.target.value)}
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
            setError(null);
          }}
          error={fieldError("email")}
        />
        {unavailable && (
          <p className="text-[12.5px] leading-snug text-fg-muted">
            {unavailable} You can set up now without one: sign in later with a passkey at the https:// address, or with
            a one-time sign-in link (by email, or <code className="font-mono">lens users link</code> on the server).
          </p>
        )}
        {error && !error.field && <AuthAlert tone="error">{error.text}</AuthAlert>}
        <Button
          type="submit"
          variant="primary"
          size="lg"
          className="w-full"
          icon={unavailable ? undefined : <Fingerprint />}
          disabled={!ready || busy || unavailable === undefined}
          disabledReason={!ready ? "Fill in the setup code and your email" : undefined}
        >
          {busy
            ? unavailable
              ? "Creating…"
              : "Waiting for your passkey…"
            : unavailable
              ? "Create admin account"
              : "Create admin with a passkey"}
        </Button>
      </form>
    </AuthCard>
  );
}
