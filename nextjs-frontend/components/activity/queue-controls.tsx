"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { RotateCcw, Trash2 } from "lucide-react";
import { useState } from "react";

import { Jobs } from "@/app/openapi-client";
import type { QueuePlace } from "@/components/activity/job-model";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";

const runs = (n: number) => `${n} ${n === 1 ? "run" : "runs"}`;

/** The queue as a whole: clear it (with undo), retry every failed run (with undo), and move one queued run. */
export function useQueueActions() {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const refresh = () => void qc.invalidateQueries({ queryKey: ["jobs"] });
  const failed = (title: string) => (e: Error) => toast({ tone: "red", title, body: e.message });

  // Undo: the runs a clear cancelled are queued again, and the runs a retry queued are cancelled again.
  const requeue = useMutation({
    mutationFn: (ids: number[]) => data(Jobs.retryJobs({ client, body: { jobs: ids, status: ["cancelled"] } })),
    onSuccess: (r) => {
      toast({ title: `Queued ${runs(r.jobs.length)} again` });
      refresh();
    },
    onError: failed("Couldn’t undo"),
  });
  const uncancel = useMutation({
    mutationFn: (ids: number[]) => data(Jobs.cancelJobs({ client, body: { jobs: ids } })),
    onSuccess: () => {
      toast({ title: "Cancelled the retries" });
      refresh();
    },
    onError: failed("Couldn’t undo"),
  });

  const clear = useMutation({
    mutationFn: (namespace?: string) => data(Jobs.cancelJobs({ client, body: { namespace: namespace || undefined } })),
    onSuccess: (r) => {
      const n = r.jobs.length;
      toast({
        title: n ? `Cleared ${runs(n)} from the queue` : "Nothing was queued",
        action: n ? { label: "Undo", onClick: () => requeue.mutate(r.jobs) } : undefined,
      });
      refresh();
    },
    onError: failed("Couldn’t clear the queue"),
  });
  const retryAll = useMutation({
    mutationFn: (namespace?: string) => data(Jobs.retryJobs({ client, body: { namespace: namespace || undefined } })),
    onSuccess: (r) => {
      const n = r.jobs.length;
      toast({
        title: n ? `Retrying ${runs(n)}` : "Nothing to retry",
        body: r.skipped
          ? `${runs(r.skipped)} left alone: the asset was deleted or already has a run going.`
          : undefined,
        action: n ? { label: "Undo", onClick: () => uncancel.mutate(r.jobs) } : undefined,
      });
      refresh();
    },
    onError: failed("Couldn’t retry"),
  });
  const move = useMutation({
    mutationFn: ({ id, place }: { id: number; place: QueuePlace }) =>
      data(Jobs.moveJob({ client, path: { jid: id }, body: place })),
    onSettled: refresh,
    onError: failed("Couldn’t move the run"),
  });
  return { clear, retryAll, move };
}

/**
 * Clear queue (queued and held runs; it asks first, then offers undo) and Retry failed (every failed run), for the
 * namespace in view or all you can process. Shown only when there is something to clear or retry.
 */
export function QueueControls({
  namespace,
  queued,
  failed,
  allowed,
}: {
  namespace?: string;
  queued: number;
  failed: number;
  allowed: boolean;
}) {
  const { clear, retryAll } = useQueueActions();
  const [asking, setAsking] = useState(false);
  if (!allowed || (!queued && !failed)) return null;
  const where = namespace ? ` in ${namespace}` : "";
  return (
    <>
      {failed > 0 && (
        <Button
          variant="secondary"
          size="sm"
          disabled={retryAll.isPending}
          onClick={() => retryAll.mutate(namespace)}
          title={`Retry the ${runs(failed)} that failed${where}, each from the step it stopped at`}
        >
          <RotateCcw /> Retry failed
        </Button>
      )}
      {queued > 0 && (
        <Button variant="secondary" size="sm" disabled={clear.isPending} onClick={() => setAsking(true)}>
          <Trash2 /> Clear queue
        </Button>
      )}
      <Dialog
        open={asking}
        onOpenChange={setAsking}
        title="Clear the queue?"
        description={`Cancels the ${runs(queued)} waiting${where}. Runs already running carry on.`}
        actions={
          <>
            <Button variant="ghost" onClick={() => setAsking(false)}>
              Keep them
            </Button>
            <Button
              variant="danger"
              disabled={clear.isPending}
              onClick={() => clear.mutate(namespace, { onSettled: () => setAsking(false) })}
            >
              {clear.isPending ? "Clearing…" : "Clear queue"}
            </Button>
          </>
        }
      >
        <p className="text-[13.5px] text-fg-secondary">You can undo it from the message that follows.</p>
      </Dialog>
    </>
  );
}
