import {
  createClient,
  createConfig,
  type Client,
} from "@/app/openapi-client/client";
import type { ClientOptions } from "@/app/openapi-client/types.gen";

/** Where the FastAPI backend lives, as seen from the Next.js server. */
export function apiBaseUrl(): string {
  return (process.env.API_BASE_URL || "http://localhost:8000").replace(
    /\/+$/,
    "",
  );
}

/**
 * A typed API client for server-side calls. Pass an access token to act as a
 * signed-in user; omit it for public endpoints (login, setup, password reset).
 *
 * Use it with the generated SDK: `Auth.status({ client: createApiClient() })`.
 */
export function createApiClient(accessToken?: string): Client {
  return createClient(
    createConfig<ClientOptions>({
      baseUrl: apiBaseUrl(),
      cache: "no-store",
      headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
    }),
  );
}

type ErrorBody = { detail?: unknown } | undefined | null;

/** Turns a FastAPI error body (`{detail: string}` or a validation list) into one message. */
export function getErrorMessage(
  error: unknown,
  fallback = "Something went wrong. Please try again.",
): string {
  const detail = (error as ErrorBody)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) =>
        item && typeof item === "object" && "msg" in item
          ? String(item.msg)
          : null,
      )
      .filter(Boolean);
    if (messages.length) return messages.join(" ");
  }
  return fallback;
}
