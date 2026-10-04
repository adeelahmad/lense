"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { History, RotateCcw, Sparkles } from "lucide-react";
import { useState } from "react";

import { Notes } from "@/app/openapi-client";
import type { NotePage as Page } from "@/app/openapi-client/types.gen";
import { Button } from "@/components/ui/button";
import { Drawer } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { relative, shortDate } from "@/lib/format";
import { cn } from "@/lib/utils";

/** A page's earlier versions, newest first: what it was before each change by a person, the assistant or the model.
 * Pick one to read it; editors put it back with Restore (which is itself kept, so it can be undone). */
export function NoteHistory({
  pid,
  canEdit,
  onClose,
  onRestored,
}: {
  pid: number;
  canEdit: boolean;
  onClose: () => void;
  onRestored: (page: Page) => void;
}) {
  const client = useApiClient();
  const toast = useToast();
  const [pick, setPick] = useState<number | null>(null);
  const list = useQuery({
    queryKey: ["note-history", pid],
    queryFn: () => data(Notes.pageHistory({ client, path: { pid } })),
  });
  const one = useQuery({
    queryKey: ["note-version", pid, pick],
    queryFn: () => data(Notes.pageVersion({ client, path: { pid, vid: pick as number } })),
    enabled: pick != null,
  });
  const restore = useMutation({
    mutationFn: (vid: number) => data(Notes.restoreVersion({ client, path: { pid, vid } })),
    onSuccess: (page) => {
      toast({ title: "Restored", body: "The page you replaced is in its history too.", tone: "green" });
      onRestored(page);
    },
    onError: (err) =>
      toast({
        title: "Couldn’t restore it",
        body: err instanceof ApiError ? err.message : "Please try again.",
        tone: "red",
      }),
  });
  const versions = list.data?.versions ?? [];
  return (
    <Drawer open onOpenChange={(o) => !o && onClose()} title="History" width={460}>
      <div className="flex flex-col gap-3 p-4">
        {list.isLoading && <Skeleton className="h-32 w-full" />}
        {list.isSuccess && !versions.length && (
          <p className="text-[13px] text-fg-muted">No earlier versions yet: they’re kept from the first change on.</p>
        )}
        <ol className="flex flex-col gap-1">
          {versions.map((v) => (
            <li key={v.id}>
              <button
                type="button"
                onClick={() => setPick(pick === v.id ? null : v.id)}
                aria-expanded={pick === v.id}
                className={cn(
                  "flex w-full flex-col items-start gap-0.5 rounded-sm px-2.5 py-2 text-left hover:bg-surface-neutral",
                  pick === v.id && "bg-blue-surface",
                )}
              >
                <span className="flex w-full items-center gap-1.5 text-[13px] font-semibold text-fg-strong">
                  {v.author === "assistant" ? (
                    <Sparkles className="size-3.5 shrink-0" aria-label="Changed by the assistant" />
                  ) : (
                    <History className="size-3.5 shrink-0" aria-hidden />
                  )}
                  <span className="truncate">{v.title || "Untitled"}</span>
                  <span className="flex-1" />
                  <span className="shrink-0 font-normal text-fg-muted" title={shortDate(v.at, true)}>
                    {relative(v.at)}
                  </span>
                </span>
                <span className="text-[12px] text-fg-muted">
                  Before a change by {v.by ?? (v.author === "assistant" ? "the assistant" : "someone")}
                  {v.why ? ` · ${v.why}` : ""}
                </span>
              </button>
              {pick === v.id && (
                <div className="mt-1 flex flex-col gap-2 rounded-sm border border-border p-2.5">
                  {one.isLoading && <Skeleton className="h-24 w-full" />}
                  {one.data && (
                    <>
                      {one.data.summary && <p className="text-[12.5px] text-fg-muted">{one.data.summary}</p>}
                      <pre className="max-h-[40vh] overflow-auto whitespace-pre-wrap break-words text-[12.5px] text-fg">
                        {one.data.body || "(empty)"}
                      </pre>
                      {canEdit && (
                        <Button
                          size="xs"
                          variant="secondary"
                          icon={<RotateCcw />}
                          disabled={restore.isPending}
                          onClick={() => restore.mutate(v.id)}
                          className="self-start"
                        >
                          Restore this version
                        </Button>
                      )}
                    </>
                  )}
                </div>
              )}
            </li>
          ))}
        </ol>
      </div>
    </Drawer>
  );
}
