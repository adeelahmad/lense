"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Speakers } from "@/app/openapi-client";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Switch } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";

/**
 * Whether the namespace recognises voices across its recordings (owners switch it). Turning it off deletes the
 * namespace's voiceprints, which can't be undone, so that one asks first; turning it back on doesn't.
 */
export function VoiceMode({ ns, mode, canManage }: { ns: string; mode: "recognize" | "separate"; canManage: boolean }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const [asking, setAsking] = useState(false);
  const save = useMutation({
    mutationFn: (to: "recognize" | "separate") =>
      data(Speakers.setNamespaceVoiceMode({ client, path: { name: ns }, body: { mode: to } })),
    onSuccess: (_, to) => {
      void qc.invalidateQueries({ queryKey: ["speakers", ns] });
      setAsking(false);
      toast({
        title: to === "separate" ? "Voices are told apart in each recording only" : "Voices are recognised again",
        tone: "green",
      });
    },
    onError: (e: Error) => toast({ title: "Couldn’t change voice recognition", body: e.message, tone: "red" }),
  });
  return (
    <div className="flex flex-col gap-1 rounded-lg border border-border px-4 py-3">
      <Switch
        checked={mode === "recognize"}
        disabled={!canManage || save.isPending}
        onCheckedChange={(on) => (on ? save.mutate("recognize") : setAsking(true))}
        label="Recognise voices across recordings"
      />
      <p className="m-0 text-[12.5px] leading-snug text-fg-secondary">
        {mode === "recognize"
          ? "Each person’s voiceprint is kept so they’re found again in new recordings."
          : "Voices are told apart in each recording, but no voiceprint is kept and nobody is matched across recordings."}
        {!canManage && " Owners of this namespace can change it."}
      </p>
      <Dialog
        open={asking}
        onOpenChange={(o) => !save.isPending && setAsking(o)}
        title="Stop recognising voices?"
        description={`The voiceprints of everyone in ${ns} are deleted, and matches waiting for review go too. Names, transcripts and who spoke when stay. This can’t be undone; turning it back on recognises people from new recordings.`}
        actions={
          <>
            <Button variant="ghost" disabled={save.isPending} onClick={() => setAsking(false)}>
              Keep recognising
            </Button>
            <Button variant="danger" disabled={save.isPending} onClick={() => save.mutate("separate")}>
              {save.isPending ? "Deleting…" : "Delete voiceprints"}
            </Button>
          </>
        }
      />
    </div>
  );
}
