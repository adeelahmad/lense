/**
 * Passkeys in the browser: turn the API's options (WebAuthn JSON) into what navigator.credentials wants, and the
 * browser's answer back into JSON for the API. Browsers that have PublicKeyCredential.parse…FromJSON and toJSON() use
 * those; older ones get the same conversion done here.
 */
import { createClient, createConfig, type Client } from "@/app/openapi-client/client";
import type { ClientOptions } from "@/app/openapi-client/types.gen";
import { throwingNetworkErrors } from "@/lib/api/client";

type Json = Record<string, unknown>;

/** The API client for the signed-out pages: this origin's /api/v1, no session. */
export function anonymousClient(): Client {
  return throwingNetworkErrors(createClient(createConfig<ClientOptions>({ baseUrl: "" })));
}

/** Whether this browser, on this address, can use passkeys at all (they need https:// or localhost). */
export function passkeysSupported(): boolean {
  return passkeysUnavailableReason() === null && typeof window !== "undefined";
}

/** Why passkeys can't be used here, for the page to say; null when they can. */
export function passkeysUnavailableReason(): string | null {
  if (typeof window === "undefined") return null;
  const host = window.location.hostname;
  if (/^[\d.]+$/.test(host) || host.includes(":") || host.startsWith("[")) {
    return "Passkeys don't work on an IP address. Open Lens at its name (like localhost, or its https:// address).";
  }
  if (!window.isSecureContext) {
    return "Passkeys only work on https:// addresses and on localhost. Open Lens at its https:// address (or localhost on this machine).";
  }
  if (typeof window.PublicKeyCredential !== "function") return "This browser doesn't support passkeys.";
  return null;
}

export function fromB64url(s: string): ArrayBuffer {
  const b64 = s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4);
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out.buffer;
}

export function toB64url(buf: ArrayBuffer | ArrayBufferView | null | undefined): string | undefined {
  if (!buf) return undefined;
  const bytes =
    buf instanceof ArrayBuffer ? new Uint8Array(buf) : new Uint8Array(buf.buffer, buf.byteOffset, buf.byteLength);
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

type Descriptor = { id: string; type: string; transports?: string[] };

const descriptors = (list: unknown) =>
  ((list as Descriptor[] | undefined) ?? []).map((d) => ({
    ...d,
    id: fromB64url(d.id),
    type: "public-key" as const,
    transports: d.transports as AuthenticatorTransport[] | undefined,
  }));

type PKC = typeof PublicKeyCredential & {
  parseCreationOptionsFromJSON?: (o: Json) => PublicKeyCredentialCreationOptions;
  parseRequestOptionsFromJSON?: (o: Json) => PublicKeyCredentialRequestOptions;
};

export function creationOptions(o: Json): PublicKeyCredentialCreationOptions {
  const user = o.user as Json;
  return {
    ...(o as object),
    challenge: fromB64url(o.challenge as string),
    user: { ...(user as object), id: fromB64url(user.id as string) },
    excludeCredentials: descriptors(o.excludeCredentials),
  } as PublicKeyCredentialCreationOptions;
}

export function requestOptions(o: Json): PublicKeyCredentialRequestOptions {
  return {
    ...(o as object),
    challenge: fromB64url(o.challenge as string),
    allowCredentials: descriptors(o.allowCredentials),
  } as PublicKeyCredentialRequestOptions;
}

/** The browser's answer as WebAuthn JSON (what the API checks). */
export function credentialJson(cred: PublicKeyCredential): Json {
  const withJson = cred as { toJSON?: () => unknown };
  if (typeof withJson.toJSON === "function") return withJson.toJSON() as Json;
  const r = cred.response as AuthenticatorResponse & {
    attestationObject?: ArrayBuffer;
    authenticatorData?: ArrayBuffer;
    signature?: ArrayBuffer;
    userHandle?: ArrayBuffer | null;
    getTransports?: () => string[];
  };
  const response: Json = { clientDataJSON: toB64url(r.clientDataJSON) };
  if (r.attestationObject) {
    response.attestationObject = toB64url(r.attestationObject);
    response.transports = r.getTransports?.() ?? [];
  } else {
    response.authenticatorData = toB64url(r.authenticatorData);
    response.signature = toB64url(r.signature);
    response.userHandle = toB64url(r.userHandle);
  }
  return {
    id: cred.id,
    rawId: toB64url(cred.rawId),
    type: cred.type,
    response,
    authenticatorAttachment: cred.authenticatorAttachment ?? undefined,
    clientExtensionResults: cred.getClientExtensionResults?.() ?? {},
  };
}

/** Make a passkey with the API's creation options; the answer as JSON. */
export async function createPasskey(options: Json): Promise<Json> {
  const P = window.PublicKeyCredential as PKC;
  const publicKey = P.parseCreationOptionsFromJSON ? P.parseCreationOptionsFromJSON(options) : creationOptions(options);
  const cred = (await navigator.credentials.create({ publicKey })) as PublicKeyCredential | null;
  if (!cred) throw new DOMException("No passkey was made.", "NotAllowedError");
  return credentialJson(cred);
}

/** Sign with a passkey (the browser lists the ones for this site); the answer as JSON. */
export async function signWithPasskey(options: Json, signal?: AbortSignal): Promise<Json> {
  const P = window.PublicKeyCredential as PKC;
  const publicKey = P.parseRequestOptionsFromJSON ? P.parseRequestOptionsFromJSON(options) : requestOptions(options);
  const cred = (await navigator.credentials.get({ publicKey, signal })) as PublicKeyCredential | null;
  if (!cred) throw new DOMException("No passkey was chosen.", "NotAllowedError");
  return credentialJson(cred);
}

/** The options for signing with a passkey that also ask it for a PRF secret (the salt arrives base64url). */
export function prfRequestOptions(o: Json): PublicKeyCredentialRequestOptions {
  const out = requestOptions(o);
  const first = ((o.extensions as Json | undefined)?.prf as { eval?: { first?: string } } | undefined)?.eval?.first;
  if (first) {
    out.extensions = {
      ...(out.extensions ?? {}),
      prf: { eval: { first: fromB64url(first) } },
    } as AuthenticationExtensionsClientInputs;
  }
  return out;
}

type PrfResults = { prf?: { enabled?: boolean; results?: { first?: ArrayBuffer | ArrayBufferView | string } } };

/**
 * Sign with a passkey and get the PRF secret it makes for the options' salt (vaults: docs/encryption.md#vaults). The
 * secret travels beside the answer, never inside it: `prf` is base64url, or "" when the passkey can't make one.
 */
export async function signWithPasskeyPrf(
  options: Json,
  signal?: AbortSignal,
): Promise<{ credential: Json; prf: string }> {
  const publicKey = prfRequestOptions(options);
  const cred = (await navigator.credentials.get({ publicKey, signal })) as PublicKeyCredential | null;
  if (!cred) throw new DOMException("No passkey was chosen.", "NotAllowedError");
  const first = (cred.getClientExtensionResults?.() as PrfResults | undefined)?.prf?.results?.first;
  const prf = typeof first === "string" ? first : (toB64url(first) ?? "");
  const credential = credentialJson(cred);
  const ext = { ...((credential.clientExtensionResults as Json | undefined) ?? {}) };
  delete ext.prf;
  return { credential: { ...credential, clientExtensionResults: ext }, prf };
}

/** What to tell someone when the browser's passkey prompt fails. */
export function passkeyErrorMessage(err: unknown, making = false): string {
  const name = err instanceof DOMException || err instanceof Error ? err.name : "";
  if (name === "NotAllowedError" || name === "AbortError") {
    return making
      ? "No passkey was made: the prompt was closed or timed out."
      : "The passkey prompt was closed or timed out.";
  }
  if (name === "InvalidStateError") return "This device already has a passkey for this account.";
  if (name === "SecurityError") return passkeysUnavailableReason() ?? "This address can't use passkeys.";
  if (name === "NotSupportedError") return "This device can't make a passkey. Try your phone or a password manager.";
  return err instanceof Error && err.message ? err.message : "Something went wrong with the passkey.";
}

/** A suggested name for a new passkey: the kind of device it's made on. */
export function deviceName(ua = typeof navigator === "undefined" ? "" : navigator.userAgent): string {
  if (/iPhone/.test(ua)) return "iPhone";
  if (/iPad/.test(ua)) return "iPad";
  if (/Android/.test(ua)) return "Android phone";
  if (/Macintosh|Mac OS X/.test(ua)) return "Mac";
  if (/Windows/.test(ua)) return "Windows PC";
  if (/CrOS/.test(ua)) return "Chromebook";
  if (/Linux/.test(ua)) return "Linux computer";
  return "Passkey";
}
