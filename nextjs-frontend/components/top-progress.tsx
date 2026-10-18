"use client";

import { useIsFetching } from "@tanstack/react-query";
import { useEffect } from "react";

import { setLoading } from "@/lib/progress";

/** Runs the loading bar while a query has no answer yet: a page's first load, a new search. Refreshing what's already
 * on screen (polling, refetching after a change) runs in the background, without the bar. Page changes run it from
 * instrumentation-client.ts. */
export function DataProgress() {
  const busy = useIsFetching({ predicate: (q) => q.state.status === "pending" }) > 0;
  useEffect(() => {
    setLoading("data", busy);
    return () => setLoading("data", false);
  }, [busy]);
  return null;
}
