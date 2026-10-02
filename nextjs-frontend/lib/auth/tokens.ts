import { Auth, type TokenPair, type UserPublic } from "@/app/openapi-client";
import { createApiClient } from "@/lib/api/client";

/** Refresh the access token this long before it expires. */
export const REFRESH_MARGIN_MS = 60_000;

export type SessionTokens = {
  accessToken: string;
  refreshToken: string;
  /** Epoch milliseconds when the access token expires. */
  expiresAt: number;
};

export type SessionUser = {
  id: string;
  email: string;
  name: string | null;
  admin: boolean;
};

export function tokensFromPair(pair: TokenPair, now = Date.now()): SessionTokens {
  return {
    accessToken: pair.access_token,
    refreshToken: pair.refresh_token,
    expiresAt: now + pair.expires_in * 1000,
  };
}

export function sessionUser(user: UserPublic): SessionUser {
  return {
    id: String(user.id),
    email: user.email,
    name: user.name ?? null,
    admin: Boolean(user.admin),
  };
}

export function needsRefresh(expiresAt: number, now = Date.now()): boolean {
  return now >= expiresAt - REFRESH_MARGIN_MS;
}

/*
 * One browser can send several requests carrying the same session cookie at
 * once (the proxy, a page render, useSession polling), and only the proxy and
 * route handlers can write the refreshed cookie back. Each refresh token works
 * once, so share a single backend call per refresh token and keep its result for
 * a short while: later readers of the old cookie get the same new pair instead
 * of spending the token again. The backend's 60s reuse grace covers requests that
 * land on other server processes.
 */
const REUSE_MS = 30_000;
const recent = new Map<string, { promise: Promise<TokenPair | null>; at: number }>();

/**
 * Swaps a refresh token for a new pair. Resolves to `null` when the backend says
 * the session has ended (401); rejects on transient failures (network, 5xx) so
 * the caller can keep the old token and try again later.
 */
export function refreshTokens(refreshToken: string): Promise<TokenPair | null> {
  const now = Date.now();
  for (const [key, entry] of recent) {
    if (now - entry.at > REUSE_MS) recent.delete(key);
  }
  const hit = recent.get(refreshToken);
  if (hit) return hit.promise;

  const promise = Auth.refresh({
    client: createApiClient(),
    body: { refresh_token: refreshToken },
  }).then(({ data, response }) => {
    if (data) return data;
    if (response?.status === 401) return null;
    throw new Error(`Token refresh failed with status ${response?.status}`);
  });
  recent.set(refreshToken, { promise, at: now });
  promise.catch(() => recent.delete(refreshToken));
  return promise;
}

/** Ends the backend session behind a refresh token. Never throws. */
export async function endBackendSession(refreshToken: string): Promise<void> {
  try {
    await Auth.logout({
      client: createApiClient(),
      body: { refresh_token: refreshToken },
    });
  } catch (err) {
    console.warn("Backend logout failed:", err);
  }
}
