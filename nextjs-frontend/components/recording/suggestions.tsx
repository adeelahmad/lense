"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, Split, X } from "lucide-react";

import { Resources, type Suggestion } from "@/app/openapi-client";
import { rk } from "@/components/recording/hooks";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";

/** What accepting a suggestion does, in words: "Add the tag finance", "Move into Finance / Budgets". */
export function suggestionLabel(s: Pick<Suggestion, "kind" | "label">): string {
  if (s.kind === "tag") return `Add the tag ${s.label}`;
  if (s.kind === "content_type") return `It’s a ${s.label}`;
  return `Move into ${s.label}`;
}

/** What a decision model thought fits this resource but wasn't sure enough to do (editors accept or dismiss). */
export function Suggestions({ rid, suggestions }: { rid: number; suggestions: Suggestion[] }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const done = () => {
    void qc.invalidateQueries({ queryKey: rk.detail(rid) });
    void qc.invalidateQueries({ queryKey: ["recording-content-type", rid] });
    void qc.invalidateQueries({ queryKey: ["recordings"] });
  };
  const settle = useMutation({
    mutationFn: (v: { sid: string; accept: boolean }) =>
      data(
        v.accept
          ? Resources.acceptSuggestion({ client, path: { rid, sid: v.sid } })
          : Resources.dismissSuggestion({ client, path: { rid, sid: v.sid } }),
      ),
    onSuccess: done,
    onError: (e: Error) => {
      toast({ tone: "red", title: "Couldn’t do that", body: e.message });
      done();
    },
  });
  if (!suggestions.length) return null;
  return (
    <section className="flex flex-col gap-0.5 rounded-md border border-border px-4 py-3.5" aria-label="Suggestions">
      <h3 className="flex items-center gap-2 pb-1 text-[13px] font-bold leading-none">
        <Split aria-hidden className="size-[15px] text-fg-secondary" />
        Suggestions
      </h3>
      <p className="m-0 pb-1.5 text-[12.5px] leading-snug text-fg-muted">
        The decision model thinks these fit, but wasn’t sure enough to do them itself.
      </p>
      <ul className="m-0 list-none p-0">
        {suggestions.map((s) => (
          <li key={s.id} className="flex items-center gap-2 border-t border-border py-1.5 text-[13px]">
            <span className="min-w-0 flex-1 break-words text-fg">{suggestionLabel(s)}</span>
            <span className="tabular text-[12px] text-fg-muted">{Math.round(s.p * 100)}% sure</span>
            <Button
              size="sm"
              variant="secondary"
              icon={<Check />}
              disabled={settle.isPending}
              onClick={() => settle.mutate({ sid: s.id, accept: true })}
            >
              Accept
            </Button>
            <Button
              size="sm"
              variant="ghost"
              icon={<X />}
              aria-label={`Dismiss: ${suggestionLabel(s)}`}
              disabled={settle.isPending}
              onClick={() => settle.mutate({ sid: s.id, accept: false })}
            />
          </li>
        ))}
      </ul>
    </section>
  );
}
