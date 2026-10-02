"use client";

import { useSession } from "next-auth/react";
import { useMemo } from "react";

import { createClient, createConfig, type Client } from "@/app/openapi-client/client";
import type { ClientOptions } from "@/app/openapi-client/types.gen";
import { throwingNetworkErrors } from "@/lib/api/client";
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
 *   useQuery({ queryKey: ["recordings"], queryFn: () => data(Resources.listRecordings({ client })) });
 */
export function useApiClient(): Client {
  const { data: session } = useSession();
  const token = session?.accessToken;
  return useMemo(
    () =>
      throwingNetworkErrors(
        createClient(
          createConfig<ClientOptions>({
            baseUrl: "",
            headers: token ? { Authorization: `Bearer ${token}` } : {},
            fetch: fetchWithReauth,
          }),
        ),
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

type Result<T> = { data?: T; error?: unknown; response?: Response };

async function unwrap<T>(call: Promise<Result<T>>): Promise<{ data: T; response: Response }> {
  let r: Result<T>;
  try {
    r = await call;
  } catch {
    throw new ApiError(0, message(null, 0));
  }
  const status = r.response?.status ?? 0;
  if (r.error !== undefined || !r.response?.ok) throw new ApiError(status, message(r.error, status), r.error);
  return { data: r.data as T, response: r.response };
}

/** Unwrap a generated SDK call: returns the data or throws ApiError (so React Query sees failures). */
export async function data<T>(call: Promise<Result<T>>): Promise<T> {
  return (await unwrap(call)).data;
}

/** A page of a list endpoint, with how many match in all (its X-Total-Count header). */
export type Page<T> = { items: T[]; total: number };

/** Unwrap a list call that counts its matches in the X-Total-Count header. */
export async function page<T>(call: Promise<Result<T[]>>): Promise<Page<T>> {
  const { data: items, response } = await unwrap(call);
  const total = Number.parseInt(response.headers.get("x-total-count") ?? "", 10);
  return { items, total: Number.isNaN(total) ? items.length : total };
}
