"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { Recordings } from "@/app/openapi-client";
import { EmbedBuilder } from "@/components/sharing/embed-builder";
import { ShareLinks, type CreatedLink } from "@/components/sharing/share-links";
import { Dialog } from "@/components/ui/dialog";
import { Tabs } from "@/components/ui/tabs";
import { data, useApiClient } from "@/lib/api/browser";
import { needRole, useArchive } from "@/lib/hooks/session";

export type ShareEmbedDialogProps = {
  recordingId: number;
  open: boolean;
  onOpenChange(open: boolean): void;
  /** Where the player should start (the Recording page passes the playhead). */
  startMs?: number;
};

/**
 * Share / Embed (SH1–SH3) for one recording: create a share link with an expiry, see it once with its iframe
 * snippet, list and revoke links, and build an embed (width, start, where it goes) with a live preview of the
 * real player (/embed/<id>, served by the API).
 */
export function ShareEmbedDialog({ recordingId, open, onOpenChange, startMs }: ShareEmbedDialogProps) {
  const client = useApiClient();
  const { can } = useArchive();
  const [tab, setTab] = useState<"share" | "embed">("share");
  const [created, setCreated] = useState<CreatedLink | null>(null);
  useEffect(() => {
    if (open) setTab("share");
  }, [open]);
  useEffect(() => setCreated(null), [recordingId]);

  const rec = useQuery({
    queryKey: ["recording", recordingId],
    queryFn: () => data(Recordings.getRecording({ client, path: { rid: recordingId } })),
    enabled: open,
    staleTime: 60_000,
  });
  const title = (rec.data?.title as string | undefined) ?? `Recording ${recordingId}`;
  const ns = (rec.data?.namespace as string | undefined) ?? null;
  const canShare = rec.isSuccess && can("editor", ns);
  const whyNot = rec.isSuccess ? needRole("editor", ns) : "Loading the recording…";
  const startSec = startMs ? Math.floor(startMs / 1000) : undefined;

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={tab === "share" ? (created ? "Share link created" : `Share ${title}`) : "Embed builder"}
      className={tab === "embed" ? "max-w-[1120px]" : "max-w-[560px]"}
    >
      <Tabs
        aria-label="Share or embed"
        size="sm"
        className="-mt-2"
        value={tab}
        onChange={(v) => setTab(v as "share" | "embed")}
        items={[
          { value: "share", label: "Share link" },
          { value: "embed", label: "Embed" },
        ]}
      />
      {rec.error ? (
        <p role="alert" className="rounded-sm border border-red-border bg-red-surface px-3 py-2 text-[13px] text-red-dark">
          Couldn’t load the recording: {(rec.error as Error).message}
        </p>
      ) : tab === "share" ? (
        <ShareLinks
          recordingId={recordingId}
          title={title}
          namespace={ns}
          canShare={canShare}
          whyNot={whyNot}
          created={created}
          onCreated={setCreated}
          onClose={() => onOpenChange(false)}
          onCustomise={() => setTab("embed")}
          startSec={startSec}
        />
      ) : (
        <EmbedBuilder recordingId={recordingId} title={title} canShare={canShare} whyNot={whyNot} created={created} onCreated={setCreated} startSec={startSec} />
      )}
    </Dialog>
  );
}
