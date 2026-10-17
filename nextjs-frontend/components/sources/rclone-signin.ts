import { Sources } from "@/app/openapi-client";
import type { Client } from "@/app/openapi-client/client";
import type { RcloneSignIn, RcloneSignInStart } from "@/app/openapi-client/types.gen";
import { data } from "@/lib/api/browser";

/** A sign-in through rclone on the server (POST /sources/rclone-signin), waiting for the browser to come back. */
export type RcloneWaiting = { id: string; url: string; port: number; opened: boolean };

/**
 * Sign in through rclone on the server: the service's page opens in a new tab (opened now, in the click, or it would
 * be blocked), where the person signs in and allows rclone. The service then sends that tab to rclone's address on
 * the server, http://127.0.0.1:53682/…, which loads only when the browser runs there too; RcloneWait takes the address
 * from there.
 */
export async function startRclone(client: Client, body: RcloneSignInStart): Promise<RcloneWaiting> {
  const tab = window.open("", "_blank");
  let got: RcloneSignIn;
  try {
    got = await data(Sources.startRcloneSignin({ client, body }));
  } catch (e) {
    tab?.close();
    throw e;
  }
  if (tab) {
    tab.opener = null; // the service's page can't reach back into Lens
    tab.location.href = got.url;
  }
  return { id: got.id, url: got.url, port: got.port, opened: Boolean(tab) };
}

/** An address the service sent the browser back to, for rclone on the server (its code, or why there's none). */
export function isRcloneReturn(text: string, port: number): boolean {
  let u: URL;
  try {
    u = new URL(text.trim());
  } catch {
    return false;
  }
  return (
    u.protocol === "http:" &&
    (u.hostname === "127.0.0.1" || u.hostname === "localhost") &&
    u.port === String(port) &&
    (u.searchParams.has("code") || u.searchParams.has("error"))
  );
}
