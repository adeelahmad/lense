"use client";

import { useSession } from "next-auth/react";
import { useMemo } from "react";

import { createClient, createConfig, type Client } from "@/app/openapi-client/client";
import type { ClientOptions } from "@/app/openapi-client/types.gen";

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
  if (Array.isArray(detail)) return detail.map((d) => (d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg) : "")).filter(Boolean).join(" ");
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
