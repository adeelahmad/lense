"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { AuthAlert, AuthCard } from "@/components/auth/auth-card";
import { CHANNEL, type Connected } from "@/components/sources/signin";

function sentence(s: string): string {
  const t = s.trim();
  return t ? t[0].toUpperCase() + t.slice(1) + (/[.!?]$/.test(t) ? "" : ".") : t;
}

/**
 * The end of signing a connection in at its service: tells the page that opened this window (same site, over a
 * BroadcastChannel) and closes. Opened any other way (a link an agent sent, or popups blocked), it says how it went
 * and links on to the connection.
 */
export function ConnectedReturn() {
  const [got, setGot] = useState<Omit<Connected, "tag"> | null>(null);

  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    const connected = q.get("connected");
    const now = connected
      ? { connected: Number(connected) }
      : { error: q.get("connect_error") || "Signing in didn’t finish." };
    setGot(now);
    const tag = q.get("tag");
    if (tag && typeof BroadcastChannel !== "undefined") {
      const channel = new BroadcastChannel(CHANNEL);
      channel.postMessage({ tag, ...now } satisfies Connected);
      channel.close();
      window.close(); // only closes a window a page opened; otherwise this page stays and says how it went
    }
  }, []);

  if (!got) return <AuthCard title="Connecting…">{null}</AuthCard>;
  return (
    <AuthCard
      title={got.error ? "Couldn’t connect" : "Connected"}
      footer={
        <Link
          href={got.connected ? `/sources?connected=${got.connected}` : "/sources"}
          className="text-fg-secondary underline underline-offset-2 hover:text-fg"
        >
          {got.connected ? "Pick a folder to import" : "Back to Lens"}
        </Link>
      }
    >
      {got.error ? (
        <AuthAlert tone="error">{sentence(got.error)}</AuthAlert>
      ) : (
        <p className="text-[13px] leading-normal text-fg-secondary">
          Lens can read it now. You can close this window, or pick a folder whose files Lens should import.
        </p>
      )}
    </AuthCard>
  );
}
