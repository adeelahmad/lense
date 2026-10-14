import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { beforeEach, test } from "node:test";

// the extension's storage, as the browser keeps it
const area = new Map();
globalThis.chrome = {
  storage: {
    local: {
      async get(key) {
        return area.has(key) ? { [key]: structuredClone(area.get(key)) } : {};
      },
      async set(items) {
        for (const [k, v] of Object.entries(items)) area.set(k, structuredClone(v));
      },
      async remove(key) {
        area.delete(key);
      },
    },
  },
};
const account = await import("../src/core/account.js");

const SERVER = "https://lens.test";
const REDIRECT = "https://abc.chromiumapp.org/lens";
const json = (status, body) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const b64url = (buf) => buf.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

/** A stand-in Lens: discovery, registration, consent, tokens and /auth/me, counting what it was asked. */
function fakeLens({ discovery = 200, refresh = 200 } = {}) {
  const lens = { registered: 0, refreshed: 0, revoked: [], codes: new Map(), calls: [], access: "at-1", refreshToken: "rt-1" };
  lens.fetchImpl = async (url, init = {}) => {
    lens.calls.push({ url, init });
    const body = init.body instanceof URLSearchParams ? Object.fromEntries(init.body) : init.body ? JSON.parse(init.body) : null;
    if (url === `${SERVER}/.well-known/oauth-authorization-server`) {
      if (discovery !== 200) return json(discovery, { detail: "not found" });
      return json(200, {
        authorization_endpoint: `${SERVER}/oauth/authorize`,
        token_endpoint: `${SERVER}/api/v1/oauth/token`,
        registration_endpoint: `${SERVER}/api/v1/oauth/register`,
        revocation_endpoint: `${SERVER}/api/v1/oauth/revoke`,
      });
    }
    if (url.endsWith("/oauth/register")) {
      lens.registered += 1;
      assert.deepEqual(body.redirect_uris, [REDIRECT]);
      assert.equal(body.token_endpoint_auth_method, "none");
      return json(201, { client_id: `client-${lens.registered}` });
    }
    if (url.endsWith("/oauth/token") && body.grant_type === "authorization_code") {
      const challenge = lens.codes.get(body.code);
      assert.ok(challenge, "a code Lens gave out");
      assert.equal(b64url(createHash("sha256").update(body.code_verifier).digest()), challenge, "the PKCE verifier matches");
      return json(200, { access_token: lens.access, refresh_token: lens.refreshToken, expires_in: 3600 });
    }
    if (url.endsWith("/oauth/token") && body.grant_type === "refresh_token") {
      lens.refreshed += 1;
      if (refresh !== 200) return json(refresh, { error: "invalid_grant" });
      assert.equal(body.refresh_token, lens.refreshToken);
      lens.access = `at-${lens.refreshed + 1}`;
      lens.refreshToken = `rt-${lens.refreshed + 1}`;
      return json(200, { access_token: lens.access, refresh_token: lens.refreshToken, expires_in: 3600 });
    }
    if (url.endsWith("/oauth/revoke")) {
      lens.revoked.push(body.token);
      return json(200, { ok: true });
    }
    if (url === `${SERVER}/api/v1/auth/me`) {
      const ok = init.headers.Authorization === `Bearer ${lens.access}` || init.headers.Authorization === "Bearer la_token";
      return ok ? json(200, { user: { email: "ada@example.com", name: "Ada" } }) : json(401, { detail: "sign in" });
    }
    if (url === `${SERVER}/api/v1/chats`) {
      return init.headers.Authorization === `Bearer ${lens.access}` || init.headers.Authorization === "Bearer la_token"
        ? json(200, [])
        : json(401, { detail: "sign in" });
    }
    return json(404, { detail: "not found" });
  };
  // the consent page: the person says yes, and the browser comes back to the extension with a code
  lens.launch = async ({ url, interactive }) => {
    assert.equal(interactive, true);
    const u = new URL(url);
    assert.equal(u.searchParams.get("code_challenge_method"), "S256");
    assert.equal(u.searchParams.get("redirect_uri"), REDIRECT);
    assert.equal(u.searchParams.get("scope"), "read write");
    const code = `code-${lens.codes.size + 1}`;
    lens.codes.set(code, u.searchParams.get("code_challenge"));
    return `${REDIRECT}?code=${code}&state=${u.searchParams.get("state")}`;
  };
  lens.signIn = () => account.signIn(SERVER, { fetchImpl: lens.fetchImpl, launch: lens.launch, redirectUri: REDIRECT });
  return lens;
}

beforeEach(() => area.clear());

test("signing in registers the extension once, uses PKCE and keeps who signed in", async () => {
  const lens = fakeLens();
  const me = await lens.signIn();
  assert.equal(me.email, "ada@example.com");
  assert.equal(me.accessToken, "at-1");
  assert.equal(me.clientId, "client-1");
  assert.equal((await account.load()).refreshToken, "rt-1");
  await lens.signIn(); // again: the registration is reused
  assert.equal(lens.registered, 1);
});

test("a sign-in that comes back for another request, or says no, is refused", async () => {
  const lens = fakeLens();
  const elsewhere = async ({ url }) => `${REDIRECT}?code=x&state=not-${new URL(url).searchParams.get("state")}`;
  await assert.rejects(account.signIn(SERVER, { fetchImpl: lens.fetchImpl, launch: elsewhere, redirectUri: REDIRECT }), /another request/);
  const no = async ({ url }) => `${REDIRECT}?error=access_denied&state=${new URL(url).searchParams.get("state")}`;
  await assert.rejects(account.signIn(SERVER, { fetchImpl: lens.fetchImpl, launch: no, redirectUri: REDIRECT }), /you said no/);
  const closed = async () => {
    throw new Error("The user did not approve access.");
  };
  await assert.rejects(account.signIn(SERVER, { fetchImpl: lens.fetchImpl, launch: closed, redirectUri: REDIRECT }), /cancelled/);
  assert.equal(await account.load(), null);
});

test("a Lens with app sign-in off says where to turn it on", async () => {
  const lens = fakeLens({ discovery: 404 });
  await assert.rejects(lens.signIn(), /Settings → API keys/);
});

test("an access token about to run out is renewed once, however many ask", async () => {
  const lens = fakeLens();
  await lens.signIn();
  await chrome.storage.local.set({ account: { ...(await account.load()), expiresAt: Date.now() + 1000 } });
  const tokens = await Promise.all([1, 2, 3].map(() => account.accessToken({ fetchImpl: lens.fetchImpl })));
  assert.deepEqual(tokens, ["at-2", "at-2", "at-2"]);
  assert.equal(lens.refreshed, 1);
  assert.equal((await account.load()).refreshToken, "rt-2");
});

test("a 401 renews the token and tries once more; an ended grant signs out", async () => {
  const lens = fakeLens();
  await lens.signIn();
  lens.access = "at-revoked-elsewhere"; // Lens no longer takes the token the extension holds
  const res = await account.api("/chats", {}, { fetchImpl: lens.fetchImpl });
  assert.equal(res.status, 200);
  assert.equal(lens.refreshed, 1);

  const ended = fakeLens({ refresh: 400 });
  await ended.signIn();
  ended.access = "gone";
  await assert.rejects(account.api("/chats", {}, { fetchImpl: ended.fetchImpl }), account.SignedOut);
  assert.equal(await account.load(), null);
});

test("signing out hands the refresh token back to Lens", async () => {
  const lens = fakeLens();
  await lens.signIn();
  await account.signOut({ fetchImpl: lens.fetchImpl });
  assert.deepEqual(lens.revoked, ["rt-1"]);
  assert.equal(await account.load(), null);
  await assert.rejects(account.api("/chats", {}, { fetchImpl: lens.fetchImpl }), account.SignedOut);
});

test("a browser without the sign-in page signs in with an API token, which is never renewed", async () => {
  const lens = fakeLens();
  const me = await account.signInWithToken(SERVER, " la_token ", { fetchImpl: lens.fetchImpl });
  assert.equal(me.apiToken, true);
  assert.equal(me.name, "Ada");
  assert.equal((await account.api("/chats", {}, { fetchImpl: lens.fetchImpl })).status, 200);
  assert.equal(lens.refreshed, 0);
  await assert.rejects(account.signInWithToken(SERVER, "la_wrong", { fetchImpl: lens.fetchImpl }), /didn't accept/);
});

test("Lens's own reason and status come with an error", async () => {
  const lens = fakeLens();
  await lens.signIn();
  await assert.rejects(account.apiJson("/chats/99", {}, { fetchImpl: lens.fetchImpl }), (e) => e.status === 404 && /not found/.test(e.message));
});
