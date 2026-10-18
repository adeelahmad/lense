"use client";

import { useState } from "react";

import type { ChatMemory } from "@/app/openapi-client/types.gen";

/**
 * Under the last message a conversation's summary covers: the model reads the summary instead of the messages above
 * it (or, after /clear, nothing of them). Show summary opens the text, so what the model remembers is never hidden.
 */
export function SummaryMark({ summary }: { summary: ChatMemory }) {
  const [open, setOpen] = useState(false);
  const n = summary.covers;
  const some = `${n} message${n === 1 ? "" : "s"}`;
  const cleared = !summary.text.trim();
  return (
    <div
      role="note"
      aria-label={cleared ? "Cleared here" : "Summarised up to here"}
      className="flex flex-col gap-2 text-[12.5px] leading-snug text-fg-muted print:hidden"
    >
      <div className="flex items-center gap-3">
        <span aria-hidden className="h-px min-w-4 flex-1 bg-border" />
        <span className="text-center">
          {cleared
            ? `Cleared here: the assistant no longer reads the ${some} above.`
            : `Summarised up to here: the assistant reads a summary of the ${some} above instead of them.`}
          {!cleared && (
            <>
              {" "}
              <button
                type="button"
                className="text-fg-accent hover:underline"
                aria-expanded={open}
                onClick={() => setOpen((o) => !o)}
              >
                {open ? "Hide summary" : "Show summary"}
              </button>
            </>
          )}
        </span>
        <span aria-hidden className="h-px min-w-4 flex-1 bg-border" />
      </div>
      {open && !cleared && (
        <p className="mx-auto max-w-[560px] whitespace-pre-wrap rounded-md border border-border bg-surface px-3.5 py-3 text-[13px] text-fg-secondary">
          {summary.text}
        </p>
      )}
    </div>
  );
}
