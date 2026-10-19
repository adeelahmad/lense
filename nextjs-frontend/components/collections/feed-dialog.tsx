"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";

import { Collections } from "@/app/openapi-client";
import { feedStatus } from "@/components/collections/feed-model";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { CodeBlock, SkeletonRows } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";

/** A collection's private podcast feed for you: its address to copy into a podcast app, and a new address when the
 * old one went somewhere it shouldn't. The address is made the first time it's opened. */
export function CollectionFeedDialog({
  id,
  name,
  open,
  onOpenChange,
}: {
  id: number;
  name: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const key = ["collection-feed", id];
  const feed = useQuery({
    queryKey: key,
    queryFn: () => data(Collections.getCollectionFeed({ client, path: { cid: id } })),
    enabled: open,
  });
  const rotate = useMutation({
    mutationFn: () => data(Collections.rotateCollectionFeed({ client, path: { cid: id } })),
    onSuccess: (f) => {
      qc.setQueryData(key, f);
      toast({
        title: "New address made",
        body: "The old one stopped working: add this one to your podcast app.",
        tone: "intent",
      });
    },
    onError: (e) =>
      toast({
        title: "Couldn’t make a new address",
        body: e instanceof ApiError ? e.message : "Please try again.",
        tone: "red",
      }),
  });
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Podcast feed"
      description={`Follow “${name}” in a podcast app: add a show by its address and paste this one. Its episodes are the assets in it with audio or video that you can read.`}
      actions={
        <>
          <Button
            variant="ghost"
            icon={<RefreshCw />}
            disabled={!feed.data || rotate.isPending}
            onClick={() => rotate.mutate()}
          >
            New address
          </Button>
          <Button variant="primary" onClick={() => onOpenChange(false)}>
            Done
          </Button>
        </>
      }
    >
      {feed.isLoading ? (
        <SkeletonRows rows={2} />
      ) : feed.isError ? (
        <Banner tone="error">{feed.error.message}</Banner>
      ) : feed.data ? (
        <div className="flex flex-col gap-2">
          <CodeBlock text={feed.data.url} label="the feed’s address" />
          <p className="m-0 text-[13px] text-fg-secondary">{feedStatus(feed.data)}</p>
          <p className="m-0 text-[12.5px] text-fg-muted">
            Anyone with this address can listen, so share it like a password. New address stops this one at once.
          </p>
        </div>
      ) : null}
    </Dialog>
  );
}
