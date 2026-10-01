"use client";

import { useSession } from "next-auth/react";

import { useApiClient } from "@/lib/api/browser";

/** The API client for the pages visitors see, whether they're signed in, and whether the session is known yet. */
export function usePublicClient() {
  const client = useApiClient();
  const { status } = useSession();
  return { client, signedIn: status === "authenticated", ready: status !== "loading" };
}
