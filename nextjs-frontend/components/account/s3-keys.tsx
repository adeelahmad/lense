"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, FolderTree, Plus } from "lucide-react";
import { useState } from "react";

import { Shares } from "@/app/openapi-client";
import type { S3Key, S3KeyCreated } from "@/app/openapi-client/types.gen";
import { rcloneConfig, webdavText } from "@/components/account/s3-model";
import { tokenExpiry } from "@/components/account/token-model";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { EmptyState, SkeletonRows } from "@/components/ui/states";
import { Table, Td, Th, THead, Tr } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { relative, shortDate } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Access keys for the file tree's S3 server (docs/file-tree.md#s3): each person makes and revokes their own. */
export function S3Keys() {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const status = useQuery({ queryKey: ["shares", "s3"], queryFn: () => data(Shares.s3Status({ client })) });
  const [created, setCreated] = useState<S3KeyCreated | null>(null);
  const create = useMutation({
    mutationFn: () => data(Shares.createS3Key({ client, body: { name: "S3 key" } })),
    onSuccess: (r) => {
      setCreated(r);
      void qc.invalidateQueries({ queryKey: ["shares", "s3"] });
    },
  });
  const revoke = useMutation({
    mutationFn: (k: S3Key) => data(Shares.revokeS3Key({ client, path: { key_id: k.id } })),
    onSuccess: (_r, k) => {
      void qc.invalidateQueries({ queryKey: ["shares", "s3"] });
      toast({ title: `Revoked ${k.id}`, body: "Anything signed in with it stops working now.", tone: "green" });
    },
  });
  const keys = status.data?.keys ?? [];
  const newKey = (
    <Button variant="primary" size="sm" icon={<Plus />} disabled={create.isPending} onClick={() => create.mutate()}>
      New access key
    </Button>
  );
  return (
    <section
      id="s3"
      className="mx-auto mt-4 flex max-w-[1000px] flex-col gap-3.5 rounded-md border border-border bg-background px-4 py-5 sm:px-6"
    >
      <div className="flex flex-wrap items-start gap-2.5">
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <h2 className="text-[18px] font-bold leading-[1.2] text-fg">File share access keys</h2>
          <p className="text-[13.5px] leading-[1.4] text-fg-secondary">
            Read the archive as folders and files from any S3 client (rclone, Cyberduck, the AWS CLI). A key reads as
            you; nothing can be changed through it.
            {status.data && !status.data.enabled && " An admin turns sharing on in Settings, File shares."}
          </p>
        </div>
        {keys.length > 0 && newKey}
      </div>
      {create.isError && <Banner tone="error">{create.error.message}</Banner>}
      {status.isPending ? (
        <SkeletonRows rows={2} />
      ) : !keys.length ? (
        <EmptyState icon={<FolderTree />} title="No access keys yet" actions={newKey}>
          Make one for each computer or app you read the archive from.
        </EmptyState>
      ) : (
        <div className="overflow-hidden rounded-md border border-border">
          <Table aria-label="Your file share access keys" className="text-[13px]">
            <THead className="border-t-0">
              <tr>
                <Th>Access key</Th>
                <Th>Created</Th>
                <Th>Expires</Th>
                <Th>Last used</Th>
                <Th>
                  <span className="sr-only">Actions</span>
                </Th>
              </tr>
            </THead>
            <tbody>
              {keys.map((k) => {
                const exp = tokenExpiry(k.expires_at);
                return (
                  <Tr key={k.id} className={cn("h-12 last:border-b-0", exp.state === "expired" && "text-fg-muted")}>
                    <Td>
                      <code className="font-mono text-[12px] font-medium">{k.id}</code>
                    </Td>
                    <Td className="tabular whitespace-nowrap">{shortDate(k.created_at)}</Td>
                    <Td className="tabular whitespace-nowrap text-fg-secondary">{exp.label}</Td>
                    <Td className="tabular whitespace-nowrap text-fg-secondary">
                      {k.last_used_at ? relative(k.last_used_at) : "never"}
                    </Td>
                    <Td className="text-right">
                      <Button
                        variant="danger-ghost"
                        size="xs"
                        disabled={revoke.isPending}
                        onClick={() => revoke.mutate(k)}
                        aria-label={`Revoke ${k.id}`}
                      >
                        Revoke
                      </Button>
                    </Td>
                  </Tr>
                );
              })}
            </tbody>
          </Table>
        </div>
      )}
      <ShownOnce created={created} onClose={() => setCreated(null)} />
    </section>
  );
}

function Copyable({ label, text }: { label: string; text: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
    } catch {
      /* clipboard blocked: the text stays selectable */
    }
  };
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-[13px] font-bold text-fg-strong">{label}</span>
      <div className="flex items-start gap-2 rounded-[10px] bg-term-bg py-2.5 pl-3.5 pr-2 text-term-fg">
        <pre className="min-w-0 flex-1 select-all whitespace-pre-wrap break-all font-mono text-[12.5px] leading-normal">
          {text}
        </pre>
        <button
          type="button"
          onClick={copy}
          className={cn(
            "inline-flex h-8 shrink-0 items-center gap-1.5 rounded-pill px-3 text-[12.5px] font-bold",
            copied ? "bg-green text-white" : "text-(--term-blue) hover:bg-white/10",
          )}
        >
          {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
    </div>
  );
}

function ShownOnce({ created, onClose }: { created: S3KeyCreated | null; onClose: () => void }) {
  const host = typeof window === "undefined" ? "localhost" : window.location.hostname;
  return (
    <Dialog
      open={Boolean(created)}
      onOpenChange={(o) => {
        if (!o) onClose();
      }}
      title="Copy your key now"
      description="The secret is shown this once. Other S3 clients take the same endpoint, key and secret, with path-style addressing."
      actions={
        <Button variant="primary" onClick={onClose}>
          I’ve saved it
        </Button>
      }
    >
      {created && (
        <div className="flex flex-col gap-3">
          <Copyable label="S3, as an rclone remote" text={rcloneConfig(host, created)} />
          <Copyable label="WebDAV, to mount as a drive" text={webdavText(host, created)} />
        </div>
      )}
    </Dialog>
  );
}
