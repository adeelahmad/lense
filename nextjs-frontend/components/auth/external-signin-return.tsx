"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { AuthAlert, AuthCard } from "@/components/auth/auth-card";
import { finishSignIn } from "@/components/auth/passkey-flows";
import { safeCallbackUrl } from "@/lib/definitions";

/** A page of this site to go to next (never another site). */
export function safeNext(next: string | null | undefined, fallback = "/"): string {
  return next && safeCallbackUrl(next) === next ? next : fallback;
}

function sentence(s: string): string {
  const t = s.trim();
  return t ? t[0].toUpperCase() + t.slice(1) + (/[.!?]$/.test(t) ? "" : ".") : t;
}

/**
 * The end of signing in with Google, GitHub, Microsoft or OpenID Connect: the API sends the browser here with a
 * one-time ticket (signs in and goes on), the name of an account just connected (back to the profile), or why it
 * didn't work.
 */
export function ExternalSigninReturn() {
  const [error, setError] = useState<string | null>(null);
  const [back, setBack] = useState("/login");

  useEffect(() => {
    const got = new URLSearchParams(window.location.hash.replace(/^#/, ""));
    // the fragment is spent: don't leave the ticket in the address bar or history
    window.history.replaceState(null, "", window.location.pathname);
    const next = safeNext(got.get("next"));
    const ticket = got.get("ticket");
    if (ticket) {
      finishSignIn(ticket, next).catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "That sign-in expired. Try again.");
      });
      return;
    }
    if (got.get("connected")) {
      window.location.replace(`${safeNext(next, "/account")}?connected=${encodeURIComponent(got.get("connected")!)}`);
      return;
    }
    setBack(safeNext(next, "/login"));
    setError(sentence(got.get("error") || "Signing in didn't finish. Try again."));
  }, []);

  return (
    <AuthCard
      title={error ? "Couldn't sign in" : "Signing in…"}
      footer={
        error ? (
          <Link href={back} className="text-fg-secondary underline underline-offset-2 hover:text-fg">
            {back === "/account" ? "Back to your profile" : "Back to sign in"}
          </Link>
        ) : undefined
      }
    >
      {error ? (
        <AuthAlert tone="error">{error}</AuthAlert>
      ) : (
        <p className="text-[13px] text-fg-secondary">One moment.</p>
      )}
    </AuthCard>
  );
}
