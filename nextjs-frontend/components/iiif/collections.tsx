"use client";

import { useQuery } from "@tanstack/react-query";
import { Copy, Download, FolderOpen, LibraryBig } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect } from "react";

import { contentStateTarget, decodeContentState, runtime } from "@/components/iiif/iiif-model";
import { Badge, RoleChip, type Role } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState, PageHeader, Skeleton, SkeletonRows } from "@/components/ui/states";
import { Table, Td, Th, THead, Tr } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { count } from "@/lib/format";
import { useArchive } from "@/lib/hooks/session";

type IiifCollection = {
  id: string;
  items?: { id: string; label?: Record<string, string[]> }[];
};
type Activity = { id: string; totalItems: number };

/** A public IIIF resource, fetched as an anonymous viewer would see it. */
export function usePublicIiif<T>(path: string, enabled = true) {
  return useQuery({
    queryKey: ["iiif-public", path],
    enabled,
    queryFn: async () => {
      const r = await fetch(path, {
        headers: { Accept: "application/ld+json" },
      });
      if (!r.ok) throw new Error(`The server answered ${r.status}`);
      return (await r.json()) as T;
    },
    staleTime: 30_000,
  });
}

export function CopyButton({ text, label }: { text: string; label: string }) {
  const toast = useToast();
  return (
    <button
      type="button"
      className="inline-flex items-center gap-1 text-[12px] font-semibold text-blue hover:underline"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          toast({ title: `${label} copied`, tone: "green" });
        } catch {
          toast({ title: "Couldn’t copy", body: text, tone: "red" });
        }
      }}
    >
      <Copy className="size-3" /> Copy
    </button>
  );
}

/** Opens a shared moment (?iiif-content=…) at its recording and time, when it points into this archive. */
function OpenMoment({ token }: { token: string }) {
  const router = useRouter();
  const target = contentStateTarget(decodeContentState(token));
  useEffect(() => {
    if (target)
      router.replace(`/resources/${target.recording}${target.t0 != null ? `?t=${Math.floor(target.t0)}` : ""}`);
  }, [target, router]);
  return target ? (
    <EmptyState icon={<LibraryBig />} title="Opening the moment…">
      Going to the recording at its time.
    </EmptyState>
  ) : (
    <EmptyState
      tone="error"
      icon={<LibraryBig />}
      title="That link isn’t a moment in this archive"
      actions={<Button onClick={() => router.replace("/iiif")}>Go to Collections</Button>}
    >
      The IIIF content state couldn’t be read, or it points to another archive.
    </EmptyState>
  );
}

/** Collections: every namespace you can read is a IIIF Collection; public ones roll up into the top-level one (IP3). */
export function CollectionsIndex() {
  const params = useSearchParams();
  const token = params.get("iiif-content");
  const { namespaces, admin, roleIn, me } = useArchive();
  const root = usePublicIiif<IiifCollection>("/iiif/collection", !token);
  const feed = usePublicIiif<Activity>("/iiif/discovery/activity", !token);

  if (token) return <OpenMoment token={token} />;
  const publicNs = new Set((root.data?.items ?? []).map((c) => c.id.split("/").pop()));

  return (
    <div className="px-4 py-5 sm:px-6">
      <PageHeader
        title="Collections"
        meta="Each namespace is a IIIF Collection"
        actions={
          <Button
            asChild={admin}
            variant="secondary"
            size="sm"
            icon={admin ? undefined : <Download />}
            disabled={!admin}
            disabledReason="Admins import from other IIIF archives"
          >
            {admin ? (
              <Link href="/iiif/import">
                <Download /> Import from IIIF
              </Link>
            ) : (
              "Import from IIIF"
            )}
          </Button>
        }
      />
      <div className="mb-4 grid gap-3 md:grid-cols-2">
        <div className="flex flex-col gap-1.5 rounded-md border border-border px-3.5 py-3">
          <b className="text-[12px] font-bold text-fg-secondary">Top-level Collection</b>
          {root.isPending ? (
            <Skeleton className="h-4 w-2/3" />
          ) : root.isError ? (
            <span className="text-[12.5px] text-red-dark">{root.error.message}</span>
          ) : (
            <>
              <code className="break-all font-mono text-[12px] leading-[1.4]">{root.data.id}</code>
              <span className="flex items-center gap-3 text-[12px] text-fg-muted">
                <CopyButton text={root.data.id} label="Collection URL" />
                {publicNs.size
                  ? `${publicNs.size} public namespace${publicNs.size === 1 ? "" : "s"}`
                  : "Nothing is public yet"}
              </span>
            </>
          )}
        </div>
        <div className="flex flex-col gap-1.5 rounded-md border border-border px-3.5 py-3">
          <b className="text-[12px] font-bold text-fg-secondary">Change feed for harvesters</b>
          {feed.isPending ? (
            <Skeleton className="h-4 w-2/3" />
          ) : feed.isError ? (
            <span className="text-[12.5px] text-red-dark">{feed.error.message}</span>
          ) : (
            <>
              <code className="break-all font-mono text-[12px] leading-[1.4]">{feed.data.id}</code>
              <span className="flex items-center gap-3 text-[12px] text-fg-muted">
                <CopyButton text={feed.data.id} label="Change feed URL" />
                IIIF Change Discovery · {count(feed.data.totalItems)} event
                {feed.data.totalItems === 1 ? "" : "s"} across the archive
              </span>
            </>
          )}
        </div>
      </div>
      {!me ? (
        <div className="rounded-md border border-border">
          <SkeletonRows rows={3} />
        </div>
      ) : !namespaces.length ? (
        <EmptyState icon={<FolderOpen />} title="No namespaces yet">
          Once you have a role in a namespace, its Collection appears here.
        </EmptyState>
      ) : (
        <div className="overflow-hidden rounded-md border border-border">
          <Table aria-label="Collections">
            <THead className="border-t-0">
              <tr>
                <Th>Collection</Th>
                <Th>Recordings</Th>
                <Th>Published</Th>
                <Th>Your role</Th>
                <Th>
                  <span className="sr-only">Actions</span>
                </Th>
              </tr>
            </THead>
            <tbody>
              {namespaces.map((n) => {
                const pub = publicNs.has(n.name);
                const role = roleIn(n.name) as Role | undefined;
                return (
                  <Tr key={n.name} className="last:border-b-0">
                    <Td>
                      <Link href={`/iiif/collections/${n.name}`} className="font-semibold text-fg hover:underline">
                        {n.name}
                      </Link>
                    </Td>
                    <Td className="tabular text-fg-secondary">
                      {count(n.recordings)} · {runtime(n.ms)}
                    </Td>
                    <Td>
                      {root.isPending ? (
                        <Skeleton className="h-4 w-20" />
                      ) : (
                        <Badge tone={pub ? "green" : "neutral"} dot>
                          {pub ? "Published" : "Not published"}
                        </Badge>
                      )}
                    </Td>
                    <Td>
                      <RoleChip role={role} />
                    </Td>
                    <Td className="whitespace-nowrap text-right">
                      <Button asChild variant="link" size="xs">
                        <Link href={`/iiif/collections/${n.name}/profile`}>Profile</Link>
                      </Button>
                      <span className="px-2 text-fg-muted">·</span>
                      <Button asChild variant="link" size="xs">
                        <Link href={`/iiif/collections/${n.name}`}>Open</Link>
                      </Button>
                    </Td>
                  </Tr>
                );
              })}
            </tbody>
          </Table>
        </div>
      )}
    </div>
  );
}
