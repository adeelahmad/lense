"use client";

import { AudioLines } from "lucide-react";

import type { Recording } from "@/app/openapi-client/types.gen";
import { offerAlign } from "@/components/recording/align-model";
import { useAlign } from "@/components/recording/hooks";
import type { JobInfo } from "@/components/recording/jobs";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";

/** On an imported transcript whose lines' times are estimated and which has its audio now: one button lines it up with
 * the audio, in the background (docs/processing.md, Lining up a transcript with its audio). The job's banner shows
 * its progress; when it finishes the page refreshes and this goes. */
export function AlignBanner({
  rid,
  rec,
  hasAudio,
  jobs,
  editable,
  reason,
  className,
}: {
  rid: number;
  rec: Pick<Recording, "times" | "aligned">;
  hasAudio: boolean;
  jobs: readonly JobInfo[];
  editable: boolean;
  reason?: string;
  className?: string;
}) {
  const align = useAlign(rid);
  if (!offerAlign(rec, hasAudio, jobs) || align.isSuccess) return null;
  return (
    <Banner
      className={className}
      title="These times are estimated from the text."
      action={
        <Button
          variant="primary"
          size="sm"
          icon={<AudioLines />}
          disabled={!editable || align.isPending}
          disabledReason={!editable ? reason : undefined}
          onClick={() => align.mutate()}
        >
          Line up with the audio
        </Button>
      }
    >
      Lining the transcript up with its audio plays each line where it&apos;s said. It transcribes the audio in the
      background; the text and speakers stay as they are.
    </Banner>
  );
}
