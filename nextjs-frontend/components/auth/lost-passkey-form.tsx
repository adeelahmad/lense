"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";

import { Auth } from "@/app/openapi-client";
import { AuthAlert, AuthCard } from "@/components/auth/auth-card";
import { AuthField } from "@/components/auth/auth-field";
import { AuthSubmit } from "@/components/auth/auth-submit";
import { anonymousClient } from "@/lib/auth/webauthn";

const EMAIL = /^\S+@\S+\.\S+$/;

/** Lost your passkey: a sign-in link by email, for adding a new one. If this server can't send email, an admin makes the link. */
export function LostPasskeyForm() {
  const [email, setEmail] = useState("");
  const [pending, setPending] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setPending(true);
    setError(null);
    try {
      const { response } = await Auth.lostPasskey({ client: anonymousClient(), body: { email: email.trim() } });
      if (response?.status === 429) setError("Too many requests; try again in a few minutes.");
      else setSent(true);
    } catch {
      setError("Can't reach the archive server. Try again in a moment.");
    } finally {
      setPending(false);
    }
  }

  return (
    <AuthCard
      title="Lost your passkey?"
      description="Enter your email and we'll send a link for adding a new passkey on this device."
      footer={
        <>
          No email? Ask an admin for a sign-in link. ·{" "}
          <Link href="/login" className="text-fg-secondary underline underline-offset-2 hover:text-fg">
            Back to sign in
          </Link>
        </>
      }
    >
      {error && <AuthAlert tone="error">{error}</AuthAlert>}
      {sent && (
        <AuthAlert tone="success">
          If {email.trim()} has an account, a sign-in link is on its way. It works once, for an hour.
        </AuthAlert>
      )}
      <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
        <AuthField
          name="email"
          label="Email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <AuthSubmit pending={pending} pendingText="Sending…" disabled={!EMAIL.test(email.trim())}>
          Email me a sign-in link
        </AuthSubmit>
      </form>
    </AuthCard>
  );
}
