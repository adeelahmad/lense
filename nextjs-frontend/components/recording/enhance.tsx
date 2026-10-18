"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Download, WandSparkles } from "lucide-react";
import { useRouter } from "next/navigation";

import { Resources } from "@/app/openapi-client";
import { useRec } from "@/components/recording/context";
import { enhanceText } from "@/components/recording/enhance-model";
import { rk, useEdits } from "@/components/recording/hooks";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { absolute } from "@/lib/format";

/**
 * An image's or a recording's Enhance row (docs/enhance.md): what the step found, a link to save the enhanced copy,
 * and, for editors, a button that enhances it now whatever the settings, then reads or transcribes it again.
 */
export function EnhanceDetail({ sound = false }: { sound?: boolean }) {
  const { id, rec, canEdit } = useRec();
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const router = useRouter();
  const e = rec.enhanced;
  const run = useMutation({
    mutationFn: () => data(Resources.enhanceRecording({ client, path: { rid: id } })),
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: rk.jobs(id) });
      toast({
        title: "Enhancing",
        body: sound
          ? "It's transcribed again from the enhanced sound; the original stays as it is."
          : "Its text is read again from the enhanced copy; the original stays as it is.",
        tone: "intent",
        action: { label: "View job", onClick: () => router.push(`/activity/${r.job}`) },
      });
    },
    onError: (err) => toast({ title: "Couldn’t enhance it", body: (err as Error).message, tone: "red" }),
  });
  return (
    <span className="flex flex-col gap-1">
      <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span>
          {enhanceText(e)}
          {e?.at ? ` · ${absolute(e.at)}` : ""}
        </span>
        {e?.enhanced && e.download && (
          <a
            href={e.download}
            download
            className="inline-flex items-center gap-1 font-semibold text-fg-accent hover:underline"
          >
            <Download aria-hidden className="size-3.5" />
            Save the copy
          </a>
        )}
        {canEdit && (
          <Button
            variant="ghost"
            size="sm"
            icon={<WandSparkles />}
            onClick={() => run.mutate()}
            disabled={run.isPending}
          >
            {sound ? "Enhance and transcribe again" : e?.enhanced ? "Enhance again" : "Enhance"}
          </Button>
        )}
      </span>
      {canEdit && sound && <EditsReplaced />}
    </span>
  );
}

/** Transcribing again makes a new transcript: say so where there are corrections it would replace. */
function EditsReplaced() {
  const { id } = useRec();
  const edits = useEdits(id);
  const n = (edits.data ?? []).filter((x) => x.after && "text" in x.after).length;
  if (!n) return null;
  return (
    <span className="text-[12.5px] leading-snug text-fg-muted">
      Transcribing again replaces your {n} corrected {n === 1 ? "line" : "lines"}.
    </span>
  );
}
