"use client";

import { getSession, useSession } from "next-auth/react";
import { useEffect, useRef } from "react";

import { SignedOutDialog } from "@/components/auth/signed-out-dialog";
import { openSignedOut, setSessionRefresher } from "@/lib/auth/reauth";

/**
 * Keeps the page when the session ends (Access AC3): instead of leaving for /login, the "You've been signed out"
 * dialog opens over the current screen, and requests that came back 401 wait for the new sign-in (lib/auth/reauth).
 * It also lets the API client refresh the session silently first (an access token that merely expired).
 */
export function SessionGuard() {
  const { data, update } = useSession();
  const ended = data?.error === "RefreshTokenError";
  const updateRef = useRef(update);
  updateRef.current = update;

  useEffect(() => {
    setSessionRefresher(async () => {
      const s = (await updateRef.current()) ?? (await getSession());
      return s && !s.error ? s.accessToken : null;
    });
    return () => setSessionRefresher(null);
  }, []);

  useEffect(() => {
    if (ended) openSignedOut();
  }, [ended]);

  return <SignedOutDialog email={data?.user?.email} />;
}
