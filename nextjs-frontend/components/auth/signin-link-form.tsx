"use client";

import { Fingerprint } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { Auth } from "@/app/openapi-client";
import { AuthAlert, AuthCard } from "@/components/auth/auth-card";
import { AuthField } from "@/components/auth/auth-field";
import { finishSignIn, linkTicket } from "@/components/auth/passkey-flows";
import { Button } from "@/components/ui/button";
import { anonymousClient, deviceName, passkeyErrorMessage, passkeysUnavailableReason } from "@/lib/auth/webauthn";

type Who = { email: string; name?: string | null };

/** Opened from a sign-in link: add a passkey on this device (fingerprint, face or PIN) and sign in. Works once. */
export function SigninLinkForm() {
  const [token, setToken] = useState<string | null>(null);
  const [who, setWho] = useState<Who | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState<string | null>(null);
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setUnavailable(passkeysUnavailableReason());
    setLabel(deviceName());
    const raw = decodeURIComponent(window.location.hash.replace(/^#/, ""));
    if (!raw) {
      setProblem("This link is incomplete. Open the whole link you were sent.");
      return;
    }
    setToken(raw);
    Auth.signinLinkInfo({ client: anonymousClient(), body: { token: raw } })
      .then(({ data }) =>
        data ? setWho(data) : setProblem("This sign-in link is invalid, used or expired. Ask an admin for a new one."),
      )
      .catch(() => setProblem("Can't reach the archive server. Try again in a moment."));
  }, []);

  async function go() {
    if (!token) return;
    setBusy(true);
    setError(null);
    try {
      await finishSignIn(await linkTicket(token, label.trim() || undefined), "/");
    } catch (err) {
      setError(passkeyErrorMessage(err, true));
      setBusy(false);
    }
  }

  return (
    <AuthCard
      title={who ? `Welcome${who.name ? `, ${who.name}` : ""}` : "Add a passkey"}
      description={
        who ? (
          <>
            Add a passkey for <strong>{who.email}</strong> on this device. You sign in with your fingerprint, face or
            device PIN: no password.
          </>
        ) : undefined
      }
      footer={
        <Link href="/login" className="text-fg-secondary underline underline-offset-2 hover:text-fg">
          Back to sign in
        </Link>
      }
    >
      {problem && <AuthAlert tone="error">{problem}</AuthAlert>}
      {unavailable && who && <AuthAlert tone="gate">{unavailable}</AuthAlert>}
      {error && <AuthAlert tone="error">{error}</AuthAlert>}
      {who && (
        <>
          <AuthField
            name="label"
            label="Name this passkey"
            hint="So you can tell your passkeys apart later"
            value={label}
            maxLength={60}
            onChange={(e) => setLabel(e.target.value)}
          />
          <Button
            variant="primary"
            size="lg"
            className="w-full"
            icon={<Fingerprint />}
            onClick={go}
            disabled={busy || Boolean(unavailable)}
            autoFocus
          >
            {busy ? "Waiting for your passkey…" : "Add a passkey and sign in"}
          </Button>
        </>
      )}
    </AuthCard>
  );
}
