/** What an app asks for when it sends someone to /oauth/authorize (the OAuth authorization request). */
export type ConsentRequest = {
  client_id: string;
  redirect_uri: string;
  response_type: string;
  code_challenge: string;
  code_challenge_method: string;
  scope?: string;
  state?: string;
  resource?: string;
};

type Params = Record<string, string | string[] | undefined>;

/** The request in the page's query, or null when it names no app or no address to return to. */
export function consentRequest(params: Params): ConsentRequest | null {
  const one = (k: string) => {
    const v = params[k];
    return (Array.isArray(v) ? v[0] : v) || undefined;
  };
  const client_id = one("client_id");
  const redirect_uri = one("redirect_uri");
  if (!client_id || !redirect_uri) return null;
  return {
    client_id,
    redirect_uri,
    response_type: one("response_type") ?? "code",
    code_challenge: one("code_challenge") ?? "",
    code_challenge_method: one("code_challenge_method") ?? "",
    scope: one("scope"),
    state: one("state"),
    resource: one("resource"),
  };
}

/** Schemes the browser would run or open itself: never followed, whatever the server said. */
const UNSAFE = new Set([
  ...["javascript:", "data:", "vbscript:", "file:", "blob:", "about:", "view-source:", "intent:", "chrome:"],
  ...["search-ms:", "smb:", "ssh:", "ftp:", "ldap:", "telnet:", "mailto:", "tel:", "sms:"],
]);
const LOOPBACK = new Set(["localhost", "127.0.0.1", "[::1]"]);

/** Whether the browser may be sent to an app's address: https, http on this machine, or the app's own scheme. */
export function safeToFollow(uri: string): boolean {
  let u: URL;
  try {
    u = new URL(uri);
  } catch {
    return false;
  }
  if (UNSAFE.has(u.protocol) || u.protocol.startsWith("ms-")) return false;
  if (u.username || u.password || uri.includes("\\")) return false;
  if (u.protocol === "http:") return LOOPBACK.has(u.hostname);
  return true;
}

/** Where an app takes people back to, as they'd recognise it: "app.example", "this computer", "the Cursor app". */
export function returnsTo(uri: string): string {
  try {
    const u = new URL(uri);
    if (u.protocol === "https:") return u.host;
    if (u.protocol === "http:") return LOOPBACK.has(u.hostname) ? "an app on this computer" : u.host;
    return `an app on this device (${u.protocol}//)`;
  } catch {
    return uri;
  }
}

export const canWrite = (scope: string | null | undefined) => (scope ?? "").split(" ").includes("write");

export const ACCESS_LABEL = (scope: string | null | undefined) => (canWrite(scope) ? "Read & write" : "Read only");

/** An app's web address as a host name, when it gave one. */
export function appHost(uri: string | null | undefined): string | null {
  if (!uri) return null;
  try {
    return new URL(uri).host;
  } catch {
    return null;
  }
}
