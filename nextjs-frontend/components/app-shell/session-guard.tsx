"use client";

import { signOut, useSession } from "next-auth/react";
import { useEffect } from "react";

/** Signs the user out once the backend has ended their session (refresh failed). */
export function SessionGuard() {
  const { data } = useSession();
  const ended = data?.error === "RefreshTokenError";
  useEffect(() => {
    if (ended) void signOut({ redirectTo: "/login" });
  }, [ended]);
  return null;
}
