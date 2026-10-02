"use client";

import { useEffect, useRef } from "react";

import { Analytics } from "@/app/openapi-client";
import { usePlayerState } from "@/components/player/media";
import { useApiClient } from "@/lib/api/browser";

/**
 * Tells the server the player started playing, once per page load, so plays can be counted (docs/analytics.md).
 * Inside a PlayerProvider. `visitor` reports through the public page's route, for people without a role.
 */
export function ReportPlay({ rid, visitor = false }: { rid: number; visitor?: boolean }) {
  const client = useApiClient();
  const { playing } = usePlayerState();
  const told = useRef(false);
  useEffect(() => {
    if (!playing || told.current) return;
    told.current = true;
    const call = visitor
      ? Analytics.playedPublic({ client, path: { rid } })
      : Analytics.played({ client, path: { rid } });
    void Promise.resolve(call).catch(() => undefined); // counting never gets in the way of playing
  }, [playing, rid, client, visitor]);
  return null;
}
