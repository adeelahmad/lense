"use client";

import { signIn } from "next-auth/react";

import { Auth } from "@/app/openapi-client";
import { getErrorMessage } from "@/lib/api/client";
import { anonymousClient, createPasskey, signWithPasskey } from "@/lib/auth/webauthn";

/** A step of a passkey flow failed; `message` is for the person. */
export class PasskeyFlowError extends Error {
  constructor(
    message: string,
    public status?: number,
  ) {
    super(message);
    this.name = "PasskeyFlowError";
  }
}

type Answer<T> = { data?: T; error?: unknown; response?: Response };

function must<T>({ data, error, response }: Answer<T>): T {
  if (data) return data;
  throw new PasskeyFlowError(sentence(getErrorMessage(error)), response?.status);
}

function sentence(s: string): string {
  const t = s.trim();
  return t ? t[0].toUpperCase() + t.slice(1) + (/[.!?]$/.test(t) ? "" : ".") : t;
}

/** Sign in with any passkey this site knows (the browser lists them): a one-time ticket. */
export async function passkeyTicket(signal?: AbortSignal): Promise<string> {
  const client = anonymousClient();
  const start = must(await Auth.passkeyOptions({ client }));
  const credential = await signWithPasskey(start.options, signal);
  const answer = await Auth.passkeyLogin({ client, body: { flow: start.flow, credential } });
  // A passkey Lens doesn't know (removed, or from before Lens was set up again): clearing the site's data doesn't
  // remove it from the password manager, so ask the browser to stop offering it.
  if (answer.response?.status === 404) await forgetPasskey(String(credential.id ?? ""));
  return must(answer).ticket;
}

type Signals = { signalUnknownCredential?: (o: { rpId: string; credentialId: string }) => Promise<void> };

/** Tells the browser (Chrome, Safari) that this site doesn't know a passkey, so its password manager drops it. */
export async function forgetPasskey(credentialId: string): Promise<void> {
  const P = (typeof window === "undefined" ? undefined : window.PublicKeyCredential) as Signals | undefined;
  if (!credentialId || !P?.signalUnknownCredential) return;
  try {
    await P.signalUnknownCredential({ rpId: window.location.hostname, credentialId });
  } catch {
    // only a hint to the browser
  }
}

/** Make the first admin with a passkey (setup code from the server log): a one-time ticket. */
export async function setupTicket(
  body: { code: string; email: string; name?: string },
  passkeyName?: string,
): Promise<string> {
  const client = anonymousClient();
  const start = must(await Auth.passkeySetupOptions({ client, body }));
  const credential = await createPasskey(start.options);
  return must(await Auth.passkeySetup({ client, body: { flow: start.flow, credential, name: passkeyName } })).ticket;
}

/** Add a passkey with a sign-in link and sign in: a one-time ticket. */
export async function linkTicket(token: string, passkeyName?: string): Promise<string> {
  const client = anonymousClient();
  const start = must(await Auth.signinLinkOptions({ client, body: { token } }));
  const credential = await createPasskey(start.options);
  return must(await Auth.signinLink({ client, body: { token, flow: start.flow, credential, name: passkeyName } }))
    .ticket;
}

/** Swap a ticket for the web app's session and go to `redirectTo` (a full page load, so everything sees the session). */
export async function finishSignIn(ticket: string, redirectTo = "/"): Promise<void> {
  const res = await signIn("ticket", { ticket, redirect: false });
  if (!res || res.error) throw new PasskeyFlowError("That sign-in expired. Try again.");
  window.location.assign(redirectTo);
}

/** Make the first admin with the setup code alone (where the browser can't make passkeys): a one-time ticket. */
export async function setupWithoutPasskeyTicket(body: { code: string; email: string; name?: string }): Promise<string> {
  return must(await Auth.setupWithoutPasskey({ client: anonymousClient(), body })).ticket;
}

/** Sign in with a sign-in link alone, without adding a passkey: a one-time ticket. */
export async function linkOnlyTicket(token: string): Promise<string> {
  return must(await Auth.signinLinkUse({ client: anonymousClient(), body: { token } })).ticket;
}
