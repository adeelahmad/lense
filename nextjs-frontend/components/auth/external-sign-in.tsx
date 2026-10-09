"use client";

import { Github, LogIn } from "lucide-react";
import { useState } from "react";

import { Auth, type ExternalProvider } from "@/app/openapi-client";
import { AuthAlert } from "@/components/auth/auth-card";
import { Button } from "@/components/ui/button";
import { getErrorMessage } from "@/lib/api/client";
import { anonymousClient } from "@/lib/auth/webauthn";
import { navigate } from "@/lib/navigate";

/** The provider's sign-in page for this browser (with the cookie that ties the round trip to it). */
export async function externalSignInUrl(key: string, next: string): Promise<string> {
  const { data, error } = await Auth.externalStart({ client: anonymousClient(), path: { key }, body: { next } });
  if (!data) throw new Error(getErrorMessage(error) || "Couldn't start signing in.");
  return data.url;
}

export function ProviderIcon({ kind }: { kind: ExternalProvider["kind"] }) {
  return kind === "github" ? <Github /> : <LogIn />;
}

/** "Continue with Google", GitHub, Microsoft or an OpenID Connect provider an admin set up (Settings › Sign-in). */
export function ExternalSignIn({
  providers,
  callbackUrl = "/",
}: {
  providers: ExternalProvider[];
  callbackUrl?: string;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (!providers.length) return null;

  async function go(key: string) {
    setBusy(key);
    setError(null);
    try {
      navigate.assign(await externalSignInUrl(key, callbackUrl));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(null);
    }
  }

  return (
    <div className="flex flex-col gap-2">
      {error && <AuthAlert tone="error">{error}</AuthAlert>}
      {providers.map((p) => (
        <Button
          key={p.key}
          size="lg"
          className="w-full"
          icon={<ProviderIcon kind={p.kind} />}
          onClick={() => void go(p.key)}
          disabled={busy !== null}
        >
          {busy === p.key ? `Opening ${p.label}…` : `Continue with ${p.label}`}
        </Button>
      ))}
    </div>
  );
}
