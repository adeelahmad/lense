"use client";

import * as D from "@radix-ui/react-dialog";
import { getSession, signIn, signOut } from "next-auth/react";
import { useEffect, useRef, useState, useSyncExternalStore, type FormEvent } from "react";

import { AuthAlert } from "@/components/auth/auth-card";
import { AuthField } from "@/components/auth/auth-field";
import { Button } from "@/components/ui/button";
import { describeHeld, discardHeld, getReauthState, signedInAgain, subscribe } from "@/lib/auth/reauth";

const CLOSED = { open: false, held: [] };

/**
 * "You've been signed out" over the current screen (Access AC3). Any request that comes back 401 is held; signing
 * in again replays it, so the page doesn't reload and nothing is lost. Signing in as someone else discards what was
 * held and goes to Home. It can't be dismissed: the page behind is inert until you choose.
 */
export function SignedOutDialog({ email }: { email?: string | null }) {
  const state = useSyncExternalStore(subscribe, getReauthState, () => CLOSED);
  const [who, setWho] = useState(email ?? "");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<{ tone: "error" | "gate"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const passwordRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (state.open) {
      setWho((w) => w || email || "");
      setPassword("");
      setError(null);
    }
  }, [state.open, email]);

  const task = describeHeld(state.held);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!who.trim() || !password) {
      setError({ tone: "error", text: "Enter your email and password." });
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await signIn("credentials", { email: who.trim(), password, redirect: false });
      if (!res || res.error) {
        setError(
          res?.code === "throttled"
            ? { tone: "gate", text: "Too many attempts; try again in a few minutes." }
            : { tone: "error", text: "Wrong email or password." },
        );
        return;
      }
      const session = await getSession();
      if (!session?.accessToken || session.error) {
        setError({ tone: "error", text: "Signed in, but the session couldn't be read. Try again." });
        return;
      }
      if (email && who.trim().toLowerCase() !== email.toLowerCase()) {
        // Another person: what was held belonged to someone else.
        discardHeld();
        window.location.assign("/");
        return;
      }
      signedInAgain(session.accessToken);
    } catch {
      setError({ tone: "error", text: "Can't reach the server. Check your connection and try again." });
    } finally {
      setBusy(false);
    }
  }

  async function someoneElse() {
    discardHeld();
    await signOut({ redirectTo: "/login" });
  }

  return (
    <D.Root open={state.open}>
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-[500] bg-[var(--scrim)] backdrop-blur-[2px] animate-fade-in" />
        <D.Content
          onEscapeKeyDown={(e) => e.preventDefault()}
          onPointerDownOutside={(e) => e.preventDefault()}
          onInteractOutside={(e) => e.preventDefault()}
          onOpenAutoFocus={(e) => {
            e.preventDefault();
            (who ? passwordRef.current : document.getElementById("so-email"))?.focus();
          }}
          className="fixed left-1/2 top-1/2 z-[501] flex w-[calc(100vw-32px)] max-w-[420px] -translate-x-1/2 -translate-y-1/2 flex-col gap-3.5 rounded-xl bg-background p-[26px] shadow-3 animate-fade-in"
        >
          <D.Title className="text-[22px] font-bold leading-[1.25] text-fg">You&apos;ve been signed out</D.Title>
          <D.Description className="text-[14px] leading-normal text-fg-secondary">
            Your session ended. Sign in again to {task ? <>finish <b className="font-bold text-fg">{task}</b></> : "carry on"} — nothing on this page has been lost.
          </D.Description>
          {error && <AuthAlert tone={error.tone}>{error.text}</AuthAlert>}
          <form method="post" onSubmit={submit} className="flex flex-col gap-3.5" noValidate>
            <AuthField id="so-email" name="email" label="Email" type="email" autoComplete="email" value={who} onChange={(e) => setWho(e.target.value)} />
            <AuthField ref={passwordRef} id="so-password" name="password" label="Password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
            <div className="flex flex-wrap items-center justify-between gap-3">
              <button type="button" onClick={someoneElse} className="text-[13px] font-semibold text-fg-secondary hover:text-fg hover:underline">
                Sign in as someone else
              </button>
              <Button type="submit" variant="primary" disabled={busy}>
                {busy ? "Signing in…" : "Sign in and continue"}
              </Button>
            </div>
          </form>
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}
