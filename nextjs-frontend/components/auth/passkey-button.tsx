"use client";

import { Fingerprint } from "lucide-react";
import { useEffect, useState } from "react";

import { AuthAlert } from "@/components/auth/auth-card";
import { finishSignIn, passkeyTicket } from "@/components/auth/passkey-flows";
import { Button } from "@/components/ui/button";
import { passkeyErrorMessage, passkeysUnavailableReason } from "@/lib/auth/webauthn";

/**
 * "Sign in with a passkey": the browser lists the passkeys made for this site (or offers a phone nearby), and a
 * fingerprint, face or device PIN signs in. Focused on arrival, so Enter is enough.
 */
export function PasskeySignIn({
  callbackUrl = "/",
  autoFocus = true,
  hideIfUnavailable = false,
}: {
  callbackUrl?: string;
  autoFocus?: boolean;
  /** Where there's another way in (passwords), leave the button out on addresses passkeys can't work on. */
  hideIfUnavailable?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState<string | null>(null);

  useEffect(() => setUnavailable(passkeysUnavailableReason()), []);

  async function go() {
    setBusy(true);
    setError(null);
    try {
      await finishSignIn(await passkeyTicket(), callbackUrl);
    } catch (err) {
      setError(passkeyErrorMessage(err));
      setBusy(false);
    }
  }

  if (unavailable && hideIfUnavailable) return null;
  return (
    <div className="flex flex-col gap-3">
      {unavailable && <AuthAlert tone="gate">{unavailable}</AuthAlert>}
      {error && <AuthAlert tone="error">{error}</AuthAlert>}
      <Button
        variant="primary"
        size="lg"
        className="w-full"
        icon={<Fingerprint />}
        onClick={go}
        disabled={busy || Boolean(unavailable)}
        autoFocus={autoFocus && !unavailable}
      >
        {busy ? "Waiting for your passkey…" : "Sign in with a passkey"}
      </Button>
    </div>
  );
}
