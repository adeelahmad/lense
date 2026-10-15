"use client";

import { useEffect, useRef } from "react";

import { Sources } from "@/app/openapi-client";
import type { Client } from "@/app/openapi-client/client";
import type { SourceSignIn } from "@/app/openapi-client/types.gen";
import { data } from "@/lib/api/browser";
import { navigate } from "@/lib/navigate";

/** What the /connected page tells the window that opened the sign-in, over this channel (same site only). */
export const CHANNEL = "lens-connect";
export type Connected = { tag: string; connected?: number; error?: string };

/**
 * Connect a source by signing in at the service (POST /sources/signin): Google's page opens in a small window, so the
 * dialog behind it keeps its place, and this resolves with the connection's id (or why it didn't work) when that
 * window comes back to /connected. It waits until then, or until `signal` aborts (the person cancels): a window that
 * went through Google can look closed to this page while it's still open. Where popups are blocked the whole page goes
 * instead, and comes back to this page with ?connected=<id> or ?connect_error=<why> (useSignInReturn).
 */
export async function signIn(
  client: Client,
  body: Omit<SourceSignIn, "next">,
  signal?: AbortSignal,
): Promise<Connected> {
  const tag = Math.random().toString(36).slice(2, 10);
  // opened now, in the click: a window opened after the request returns would be blocked as a popup
  const popup = window.open("", "lens-connect", "popup,width=520,height=680");
  const q = new URLSearchParams(window.location.search);
  q.delete("connected");
  q.delete("connect_error");
  const here = `${window.location.pathname}${q.size ? `?${q}` : ""}`;
  let url: string;
  try {
    ({ url } = await data(
      Sources.startSignin({ client, body: { ...body, next: popup ? `/connected?tag=${tag}` : here } }),
    ));
  } catch (e) {
    popup?.close();
    throw e;
  }
  if (!popup) {
    navigate.assign(url);
    return new Promise<Connected>(() => undefined); // the page is leaving
  }
  popup.location.href = url;
  return new Promise<Connected>((resolve) => {
    const channel = typeof BroadcastChannel === "undefined" ? null : new BroadcastChannel(CHANNEL);
    const done = (got: Connected) => {
      channel?.close();
      resolve(got);
    };
    if (channel)
      channel.onmessage = (e: MessageEvent<Connected>) => {
        if (e.data?.tag === tag) done(e.data);
      };
    signal?.addEventListener("abort", () => {
      popup.close();
      done({ tag, error: "cancelled" });
    });
  });
}

/**
 * Back from signing in with the whole page (popups blocked): the page it started on has ?connected=<id> or
 * ?connect_error=<why>. Hands either over once and drops both from the address.
 */
export function useSignInReturn(on: (got: { connected?: number; error?: string }) => void, enabled = true) {
  const seen = useRef(false);
  useEffect(() => {
    if (!enabled || seen.current) return;
    const q = new URLSearchParams(window.location.search);
    const connected = q.get("connected");
    const error = q.get("connect_error");
    if (!connected && !error) return;
    seen.current = true;
    q.delete("connected");
    q.delete("connect_error");
    window.history.replaceState(window.history.state, "", `${window.location.pathname}${q.size ? `?${q}` : ""}`);
    on(error ? { error } : { connected: Number(connected) });
  }, [enabled]);
}
