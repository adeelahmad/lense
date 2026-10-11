// Signing in to a Lens as its person, for the side panel: OAuth 2.1 with PKCE (docs/authentication.md#oauth), the way
// an MCP client does. The extension registers itself with the Lens, the browser shows Lens's own consent page (signed
// in with a passkey there), and the code comes back to the extension's redirect address: nothing to copy or paste.
// The tokens act as the person and stay on this device; they renew themselves, and signing out hands them back.
//
// A browser without launchWebAuthFlow (Safari) signs in with an API token the person makes in Lens instead.

const ext = globalThis.browser ?? globalThis.chrome;
const KEY = "account";
const SCOPE = "read write";
const EARLY_MS = 60_000; // renew this long before the access token runs out

export const canOAuth = () => Boolean(ext?.identity?.launchWebAuthFlow);

const b64url = (bytes) =>
  btoa(String.fromCharCode(...new Uint8Array(bytes)))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");

/** A PKCE verifier and its S256 challenge (RFC 7636). */
export async function pkce() {
  const verifier = b64url(crypto.getRandomValues(new Uint8Array(32)));
  const challenge = b64url(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier)));
  return { verifier, challenge };
}

async function json(res, what) {
  let body = {};
  try {
    body = await res.json();
  } catch {
    // not JSON
  }
  if (!res.ok) {
    const why = body.error_description || (typeof body.detail === "string" ? body.detail : "") || `${res.status} ${res.statusText}`;
    throw Object.assign(new Error(`${what}: ${why}`), { status: res.status });
  }
  return body;
}

/** Where a Lens signs apps in (RFC 8414); an error that says so when apps can't sign in there. */
export async function discover(server, { fetchImpl = fetch } = {}) {
  let res;
  try {
    res = await fetchImpl(`${server}/.well-known/oauth-authorization-server`, { credentials: "omit" });
  } catch (e) {
    throw new Error(`couldn't reach ${server}: ${e.message}`);
  }
  if (res.status === 404) throw new Error("this Lens doesn't let apps sign in: an admin turns it on in Settings → API keys");
  return json(res, "this address didn't answer as a Lens");
}

export async function load() {
  return (await ext.storage.local.get(KEY))[KEY] ?? null;
}

async function store(account) {
  if (account) await ext.storage.local.set({ [KEY]: account });
  else await ext.storage.local.remove(KEY);
  return account;
}

function tokens(account, got) {
  return {
    ...account,
    accessToken: got.access_token,
    refreshToken: got.refresh_token ?? account.refreshToken,
    expiresAt: Date.now() + (got.expires_in ?? 3600) * 1000,
  };
}

/**
 * Sign in to the Lens at `server` (its address, normalised). Registers the extension there the first time, shows the
 * consent page, and swaps the code for tokens. `launch` is identity.launchWebAuthFlow (tests pass their own).
 */
export async function signIn(server, { fetchImpl = fetch, launch, redirectUri } = {}) {
  launch ??= (opts) => ext.identity.launchWebAuthFlow(opts);
  redirectUri ??= ext.identity.getRedirectURL("lens");
  const meta = await discover(server, { fetchImpl });
  const before = await load();
  let clientId = before?.server === server && before.redirectUri === redirectUri ? before.clientId : null;
  if (!clientId) {
    const reg = await json(
      await fetchImpl(meta.registration_endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          client_name: "Lens browser extension",
          redirect_uris: [redirectUri],
          token_endpoint_auth_method: "none",
        }),
        credentials: "omit",
      }),
      "Lens didn't register the extension",
    );
    clientId = reg.client_id;
  }
  const { verifier, challenge } = await pkce();
  const state = b64url(crypto.getRandomValues(new Uint8Array(16)));
  const url = new URL(meta.authorization_endpoint);
  for (const [k, v] of Object.entries({
    response_type: "code",
    client_id: clientId,
    redirect_uri: redirectUri,
    code_challenge: challenge,
    code_challenge_method: "S256",
    scope: SCOPE,
    state,
  }))
    url.searchParams.set(k, v);
  let back;
  try {
    back = new URL(await launch({ url: url.toString(), interactive: true }));
  } catch (e) {
    throw new Error(/did not approve|cancel|closed/i.test(e.message) ? "signing in was cancelled" : e.message);
  }
  if (back.searchParams.get("state") !== state) throw new Error("the sign-in came back for another request; try again");
  if (back.searchParams.get("error")) throw new Error(back.searchParams.get("error") === "access_denied" ? "you said no in Lens" : back.searchParams.get("error"));
  const code = back.searchParams.get("code");
  if (!code) throw new Error("Lens didn't send a sign-in code back");
  const got = await json(
    await fetchImpl(meta.token_endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ grant_type: "authorization_code", code, redirect_uri: redirectUri, code_verifier: verifier, client_id: clientId }),
      credentials: "omit",
    }),
    "Lens didn't sign the extension in",
  );
  const account = tokens(
    { server, clientId, redirectUri, tokenEndpoint: meta.token_endpoint, revocationEndpoint: meta.revocation_endpoint },
    got,
  );
  return store(await withMe(account, fetchImpl));
}

/** Sign in with an API token (la_…) made in Lens, for a browser that can't show the consent page. */
export async function signInWithToken(server, token, { fetchImpl = fetch } = {}) {
  const account = { server, accessToken: token.trim(), apiToken: true };
  return store(await withMe(account, fetchImpl));
}

async function withMe(account, fetchImpl) {
  const me = await json(
    await fetchImpl(`${account.server}/api/v1/auth/me`, { headers: { Authorization: `Bearer ${account.accessToken}` }, credentials: "omit" }),
    "Lens didn't accept the sign-in",
  );
  return { ...account, email: me.user?.email ?? null, name: me.user?.name ?? null };
}

let renewing = null;

/** The access token, renewed first when it's about to run out (one renewal at a time). */
export async function accessToken({ fetchImpl = fetch, force = false } = {}) {
  const account = await load();
  if (!account) throw new SignedOut();
  if (account.apiToken || (!force && account.expiresAt - EARLY_MS > Date.now())) return account.accessToken;
  renewing ??= (async () => {
    try {
      const res = await fetchImpl(account.tokenEndpoint, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({ grant_type: "refresh_token", refresh_token: account.refreshToken, client_id: account.clientId }),
        credentials: "omit",
      });
      if (res.status === 400 || res.status === 401) {
        await store(null); // the grant ended (revoked in Lens, or it lapsed): sign in again
        throw new SignedOut();
      }
      return (await store(tokens(account, await json(res, "Lens didn't renew the sign-in")))).accessToken;
    } finally {
      renewing = null;
    }
  })();
  return renewing;
}

export class SignedOut extends Error {
  constructor() {
    super("sign in to Lens first");
    this.name = "SignedOut";
  }
}

/** fetch() to the signed-in Lens's API as its person; a 401 renews the token once and tries again. */
export async function api(path, init = {}, { fetchImpl = fetch } = {}) {
  const account = await load();
  if (!account) throw new SignedOut();
  const go = async (token) =>
    fetchImpl(`${account.server}/api/v1${path}`, {
      ...init,
      headers: { Accept: "application/json", ...(init.headers ?? {}), Authorization: `Bearer ${token}` },
      credentials: "omit",
    });
  let res = await go(await accessToken({ fetchImpl }));
  if (res.status === 401 && !account.apiToken) res = await go(await accessToken({ fetchImpl, force: true }));
  if (res.status === 401) {
    await store(null);
    throw new SignedOut();
  }
  return res;
}

/** api() for JSON answers: the body, or an Error with Lens's reason. */
export async function apiJson(path, init = {}, opts = {}) {
  const res = await api(path, init, opts);
  return json(res, "Lens said no");
}

/** Sign out: the tokens are handed back to Lens (RFC 7009) and forgotten here. */
export async function signOut({ fetchImpl = fetch } = {}) {
  const account = await load();
  await store(null);
  if (!account?.revocationEndpoint) return;
  try {
    await fetchImpl(account.revocationEndpoint, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ token: account.refreshToken, client_id: account.clientId }),
      credentials: "omit",
    });
  } catch {
    // signed out here either way; the grant lapses in Lens or is removed there
  }
}
