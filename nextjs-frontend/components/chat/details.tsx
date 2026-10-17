"use client";

import { useQuery } from "@tanstack/react-query";
import { Info } from "lucide-react";
import { useState } from "react";

import { Chats } from "@/app/openapi-client";
import type { MessageRecord } from "@/app/openapi-client/types.gen";
import { fromApiScope, scopeWords } from "@/components/chat/scope";
import { took } from "@/components/chat/steps";
import type { ToolStep } from "@/components/chat/stream";
import { actionText, Cost, ResourceCost, tokens } from "@/components/costs/costs";
import { useIsNarrow } from "@/components/library/use-media";
import { Dialog } from "@/components/ui/dialog";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/menu";
import { data, useApiClient } from "@/lib/api/browser";
import { plural } from "@/lib/format";

/** How an answer ended, in words. */
export const OUTCOME: Record<string, string> = {
  answered: "Answered after looking things up",
  direct: "Answered without looking anything up",
  limit: "Its tool rounds ran out, so it answered from what it found",
  fallback: "Answered from a search: the model can’t use tools, or none is set up",
  stopped: "Stopped before it finished",
  error: "Didn’t answer: something went wrong",
};

const ESTIMATE =
  "An estimate from the prices in Settings › Telemetry; some calls had a price but no token counts, so it’s at least this.";

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <>
      <dt className="text-fg-muted">{label}</dt>
      <dd className="m-0 min-w-0 wrap-break-word text-fg">{children}</dd>
    </>
  );
}

/** What produced an answer (its record), with the calls made for it from the activity ledger. */
export function DetailsBody({
  meta,
  steps,
  chatId,
  messageId,
}: {
  meta: MessageRecord | null | undefined;
  steps: ToolStep[];
  chatId: number | null;
  messageId: number | null;
}) {
  const client = useApiClient();
  const ledger = useQuery({
    queryKey: ["chat-message", chatId, messageId],
    queryFn: () =>
      data(Chats.getMessage({ client, path: { cid: chatId as number, mid: messageId as number } })).then(
        (m) => m.ledger ?? [],
      ),
    enabled: chatId != null && messageId != null && Boolean(meta),
    staleTime: 30_000,
  });
  if (!meta)
    return (
      <p className="m-0 text-[13px] leading-snug text-fg-secondary">
        This answer is from before Lens recorded what produced each one, so only its model and steps are known.
      </p>
    );
  const offered = meta.tools?.offered ?? [];
  const used = [...new Set(steps.map((s) => s.tool))];
  const rounds = meta.limits?.rounds;
  const calls = (ledger.data ?? []).filter((r) => r.kind === "out");
  return (
    <div className="flex flex-col gap-3">
      <dl className="m-0 grid grid-cols-[88px_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-[12.5px] leading-snug">
        <Row label="Ended">{meta.outcome ? (OUTCOME[meta.outcome] ?? meta.outcome) : "Not recorded"}</Row>
        <Row label="Model">
          {meta.model ? <span className="font-mono">{meta.model}</span> : "None set up"}
          {meta.provider ? ` on ${meta.provider}` : ""}
        </Row>
        <Row label="Scope">{scopeWords(fromApiScope(meta.scope))}</Row>
        <Row label="Tools">
          {meta.tools?.picked?.length ? `${meta.tools.picked.join(", ")} · ` : offered.length ? "Auto · " : ""}
          {offered.length ? (
            <details className="inline">
              <summary className="inline cursor-pointer text-fg-accent hover:underline">
                {plural(offered.length, "tool")} offered
              </summary>
              <span className="mt-1 block font-mono text-[11.5px] text-fg-secondary">{offered.join(", ")}</span>
            </details>
          ) : (
            "None offered"
          )}
        </Row>
        <Row label="Used">
          {rounds != null
            ? `${meta.used?.rounds ?? 0} of ${plural(rounds, "round")}, ${plural(meta.used?.tool_calls ?? 0, "tool call")}${
                meta.limits?.tool_calls != null ? ` of ${meta.limits.tool_calls}` : ""
              }`
            : "No tools"}
          {used.length > 0 && (
            <span className="block font-mono text-[11.5px] text-fg-secondary">{used.join(", ")}</span>
          )}
        </Row>
        {(meta.flagged ?? []).length > 0 && (
          <Row label="Flagged">
            <span className="text-red-dark">
              {meta.flagged!.join(", ")}: what {meta.flagged!.length === 1 ? "it" : "they"} read looked like
              instructions to an AI, so it was marked as data
            </span>
          </Row>
        )}
        {meta.tokens && (
          <Row label="Tokens">
            {tokens(meta.tokens.in ?? 0)} in · {tokens(meta.tokens.out ?? 0)} out
          </Row>
        )}
        {meta.cost_usd != null && (
          <Row label="Cost">
            {meta.cost_usd > 0 ? (
              <>
                <Cost usd={meta.cost_usd} estimate={meta.cost_estimated} why={ESTIMATE} />{" "}
                <span className="text-fg-muted">(estimated from the prices in Settings)</span>
              </>
            ) : (
              "Not costed: no price is set for its model"
            )}
          </Row>
        )}
        {meta.ms != null && <Row label="Took">{took(meta.ms)}</Row>}
        {meta.version && <Row label="Lens">{meta.version}</Row>}
      </dl>
      {calls.length > 0 && (
        <div className="flex flex-col gap-1 border-t border-border pt-2.5">
          <b className="text-[12px] font-semibold text-fg">Calls made for it</b>
          <ul className="m-0 flex list-none flex-col gap-0.5 p-0 text-[12px] text-fg-secondary">
            {calls.map((r, i) => (
              <li key={r.id ?? i} className="flex gap-2">
                <span className="min-w-0 flex-1 truncate">
                  {actionText(r)}
                  {r.ok ? "" : ` · failed${r.error ? ` (${r.error})` : ""}`}
                </span>
                <span className="shrink-0 tabular-nums">
                  {r.tokens_in != null || r.tokens_out != null
                    ? `${tokens((r.tokens_in ?? 0) + (r.tokens_out ?? 0))} tokens`
                    : ""}
                  {r.ms != null ? ` · ${took(r.ms)}` : ""}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {chatId != null && (
        <p className="m-0 border-t border-border pt-2.5 text-[12px] text-fg-secondary">
          This conversation this month: <ResourceCost resource={`chat:${chatId}`} />
        </p>
      )}
    </div>
  );
}

/** The "i" in an answer's header: what produced it, in a popover (a sheet on a phone). */
export function AnswerDetails(p: {
  meta: MessageRecord | null | undefined;
  steps: ToolStep[];
  chatId: number | null;
  messageId: number | null;
}) {
  const narrow = useIsNarrow();
  const [open, setOpen] = useState(false);
  const trigger = (
    <button
      type="button"
      aria-label="Details: what produced this answer"
      title="Details"
      onClick={narrow ? () => setOpen(true) : undefined}
      className="grid size-6 place-items-center rounded-full text-fg-muted hover:bg-surface-neutral hover:text-fg"
    >
      <Info aria-hidden className="size-3.5" />
    </button>
  );
  if (narrow)
    return (
      <>
        {trigger}
        <Dialog open={open} onOpenChange={setOpen} title="What produced this answer">
          <DetailsBody {...p} />
        </Dialog>
      </>
    );
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>{trigger}</PopoverTrigger>
      <PopoverContent align="start" className="max-h-[70vh] w-[min(380px,calc(100vw-32px))] overflow-y-auto p-4">
        <b className="mb-2.5 block text-[13px] font-bold text-fg">What produced this answer</b>
        <DetailsBody {...p} />
      </PopoverContent>
    </Popover>
  );
}
