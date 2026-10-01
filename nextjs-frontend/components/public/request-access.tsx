"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { KeyRound } from "lucide-react";
import { useState } from "react";

import { Public } from "@/app/openapi-client";
import type { PublicRecording } from "@/app/openapi-client/types.gen";
import { usePublicClient } from "@/components/public/hooks";
import { requestLine } from "@/components/public/model";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, Textarea } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { data } from "@/lib/api/browser";
import { cn } from "@/lib/utils";

/**
 * Asking a recording's owners for access (Aviary's "request access"), for someone signed in without permission: to a
 * public recording's closed parts, or to a restricted one. Shows where their latest request stands.
 */
export function RequestAccess({ rec, className }: { rec: PublicRecording; className?: string }) {
  const { client } = usePublicClient();
  const qc = useQueryClient();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [message, setMessage] = useState("");
  const ns = rec.namespace ?? "its namespace";
  const req = rec.request;
  const ask = useMutation({
    mutationFn: () =>
      data(Public.requestAccess({ client, path: { rid: rec.id }, body: { message: message.trim() || null } })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["public", "recording", rec.id] });
      setOpen(false);
      toast({ title: "Request sent", body: `The owners of ${ns} will decide.`, tone: "green" });
    },
  });
  const openDialog = () => {
    setMessage(req?.status === "pending" ? (req.message ?? "") : "");
    ask.reset();
    setOpen(true);
  };
  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-x-3 gap-y-2 rounded-md border border-border bg-background px-4 py-3",
        className,
      )}
    >
      <KeyRound aria-hidden className="size-4 shrink-0 text-fg-secondary" />
      <p className="min-w-0 flex-1 basis-[240px] text-[13.5px] leading-[1.45] text-fg">
        {requestLine(req, ns, rec.view === "locked")}
      </p>
      <Button size="sm" onClick={openDialog}>
        {req?.status === "pending"
          ? "Change your message"
          : req?.status === "declined"
            ? "Ask again"
            : "Ask for access"}
      </Button>
      <Dialog
        open={open}
        onOpenChange={setOpen}
        title="Ask for access"
        description={`The owners of ${ns} get your request and decide. Approved, you see all of this recording.`}
        actions={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button variant="primary" disabled={ask.isPending} onClick={() => ask.mutate()}>
              {ask.isPending ? "Sending…" : req?.status === "pending" ? "Update request" : "Send request"}
            </Button>
          </>
        }
      >
        <Field label="Message" hint="Optional: who you are and why you’d like access">
          {(f) => (
            <Textarea
              id={f.id}
              aria-describedby={f.describedBy}
              rows={4}
              maxLength={1000}
              value={message}
              onChange={(e) => setMessage(e.target.value)}
            />
          )}
        </Field>
        {ask.isError && <Banner tone="error">{ask.error.message}</Banner>}
      </Dialog>
    </div>
  );
}
