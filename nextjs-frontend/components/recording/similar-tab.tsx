"use client";

import { useRec } from "@/components/recording/context";
import { SimilarResultsView } from "@/components/similar/similar-list";

/** Similar tab: the resources most like this one (by meaning, key words, topics and entities), each with the moments
 * that show it, and Find more for all of them. Selecting text in the page shows what's like that instead. */
export function SimilarTab() {
  const { id } = useRec();
  return (
    <>
      <p className="text-[13px] leading-snug text-fg-secondary">
        Resources like this one. Select any text to see what&apos;s like that.
      </p>
      <SimilarResultsView basis={{ recording: id }} limit={10} />
    </>
  );
}
