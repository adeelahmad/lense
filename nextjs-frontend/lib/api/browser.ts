"use client";

import { useSession } from "next-auth/react";
import { useMemo } from "react";

import { createClient, createConfig, type Client } from "@/app/openapi-client/client";
import type { ClientOptions } from "@/app/openapi-client/types.gen";
import { holdUntilSignedIn, refreshedToken } from "@/lib/auth/reauth";

/**
 * Fetch for the API client: a 401 on a signed-in request first tries a silent session refresh, then holds the
 * request while the "You've been signed out" dialog asks for the password (Access AC3), and replays it with the
 * new token. The caller just sees the replayed response, so nothing on the page is lost.
 */
export const fetchWithReauth: typeof fetch = async (input, init) => {
  const request = input instanceof Request && !init ? input : new Request(input, init);
  const auth = request.headers.get("Authorization");
  const retry = auth ? request.clone() : null;
  const response = await fetch(request);
  if (response.status !== 401 || !retry) return response;
  const token = (await refreshedToken(auth)) ?? (await holdUntilSignedIn({ method: request.method, url: request.url }));
  const headers = new Headers(retry.headers);
  headers.set("Authorization", `Bearer ${token}`);
  return fetch(new Request(retry, { headers }));
};

/**
 * The typed API client for client components. Requests go to this origin's /api/v1, which next.config.mjs
 * rewrites to the API, with the session's access token. Use with the generated SDK and React Query:
 *
 *   const client = useApiClient();
 *   useQuery({ queryKey: ["recordings"], queryFn: () => data(Recordings.listRecordings({ client })) });
 */
export function useApiClient(): Client {
  const { data: session } = useSession();
  const token = session?.accessToken;
  return useMemo(
    () =>
      createClient(
        createConfig<ClientOptions>({
          baseUrl: "",
          headers: token ? { Authorization: `Bearer ${token}` } : {},
          fetch: fetchWithReauth,
        }),
      ),
    [token],
  );
}

/** An API error with the HTTP status and the backend's message. */
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public body?: unknown,
  ) {
    super(message);
  }
}

function message(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail))
    return detail
      .map((d) => (d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg) : ""))
      .filter(Boolean)
      .join(" ");
  if (status === 0) return "Can't reach the server. Check your connection and try again.";
  return `Request failed (${status})`;
}

/** Unwrap a generated SDK call: returns the data or throws ApiError (so React Query sees failures). */
export async function data<T>(call: Promise<{ data?: T; error?: unknown; response?: Response }>): Promise<T> {
  let r: { data?: T; error?: unknown; response?: Response };
  try {
    r = await call;
  } catch {
    throw new ApiError(0, message(null, 0));
  }
  const status = r.response?.status ?? 0;
  if (r.error !== undefined || !r.response?.ok) throw new ApiError(status, message(r.error, status), r.error);
  return r.data as T;
}
