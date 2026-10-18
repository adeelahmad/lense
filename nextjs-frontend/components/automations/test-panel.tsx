"use client";

/** Try the automation as it is in the builder, saved or not, on a recent activity its triggers would take or on a
 * kind's sample: whether its If passed, what each node did and what each action would do. Nothing is kept or sent. */
import { useMutation, useQuery } from "@tanstack/react-query";
import { FlaskConical, X } from "lucide-react";
import { useState } from "react";

import { Activities, Automations } from "@/app/openapi-client";
import type { ActivityKind } from "@/app/openapi-client/types.gen";
import { runStatus, toApi, type Draft } from "@/components/automations/model";
import { Badge } from "@/components/ui/badge";
import { Button, IconButton } from "@/components/ui/button";
import { Select } from "@/components/ui/field";
import { wouldDo, type ActionDef } from "@/components/workflows/workflow-model";
import { data, useApiClient } from "@/lib/api/browser";
import { relative } from "@/lib/format";

export function TestPanel({
  draft,
  actions,
  kinds,
  onClose,
}: {
  draft: Draft;
  actions?: ActionDef[];
  kinds: ActivityKind[];
  onClose: () => void;
}) {
  const client = useApiClient();
  const listening = [...new Set(draft.triggers.flatMap((t) => (t.on === "event" ? (t.kinds ?? []) : [])))];
  const recent = useQuery({
    queryKey: ["activities", "for-test", listening],
    queryFn: () => data(Activities.listActivities({ client, query: { kinds: listening, limit: 8 } })),
    enabled: listening.length > 0,
  });
  const items = recent.data?.items ?? [];
  const samples = listening.length
    ? listening
    : draft.triggers.some((t) => t.on === "schedule" || t.on === "at")
      ? ["clock.tick"]
      : ["webhook.received"];
  const [pick, setPick] = useState("");
  const chosen = pick || (items[0] ? `a:${items[0].id}` : `k:${samples[0]}`);
  const tryIt = useMutation({
    mutationFn: () =>
      data(
        Automations.testDraft({
          client,
          body: {
            draft: { ...toApi(draft, actions), enabled: false } as never,
            ...(chosen.startsWith("a:") ? { activity: chosen.slice(2) } : { kind: chosen.slice(2) }),
          },
        }),
      ),
  });
  const r = tryIt.data;
  const label = (k: string) => kinds.find((x) => x.name === k)?.label ?? k;
  return (
    <aside
      aria-label="Test"
      className="flex flex-col gap-3 rounded-md border border-border bg-background p-4 lg:sticky lg:top-4"
    >
      <header className="flex items-center gap-2">
        <h2 className="flex-1 text-[15px] font-bold text-fg">Test it</h2>
        <IconButton label="Close the test" size={30} onClick={onClose}>
          <X />
        </IconButton>
      </header>
      <p className="text-[12.5px] text-fg-secondary">
        Runs it as it is here, saved or not. Nothing is kept or sent: each action says what it would do.
      </p>
      <Select
        aria-label="Test on"
        value={chosen}
        onChange={(e) => setPick(e.target.value)}
        options={[
          ...items.map((a) => ({ value: `a:${a.id}`, label: `${a.summary.slice(0, 60)} · ${relative(a.published)}` })),
          ...samples.map((k) => ({ value: `k:${k}`, label: `A sample of ${label(k)}` })),
        ]}
      />
      <Button variant="primary" icon={<FlaskConical />} disabled={tryIt.isPending} onClick={() => tryIt.mutate()}>
        {tryIt.isPending ? "Testing…" : "Test"}
      </Button>
      {tryIt.error && (
        <p role="alert" className="text-[12.5px] text-red-dark">
          {(tryIt.error as Error).message}
        </p>
      )}
      {r && (
        <div className="flex flex-col gap-2 text-[13px]" aria-label="Test result">
          <div className="flex flex-wrap items-center gap-2">
            {r.status === "skipped" ? (
              <Badge tone="neutral">It wouldn’t run</Badge>
            ) : (
              <Badge tone={runStatus(r.status).tone}>{`It would end ${runStatus(r.status).label.toLowerCase()}`}</Badge>
            )}
            {r.why && <span className="text-fg-secondary">{r.why}</span>}
          </div>
          {r.error && <p className="text-red-dark">{r.error}</p>}
          {(r.intents ?? []).length > 0 && (
            <ul aria-label="What it would do" className="flex flex-col gap-1">
              {(r.intents ?? []).map((i, k) => (
                <li key={k} className="rounded-sm border border-border bg-surface px-2.5 py-1.5">
                  {wouldDo(i, actions)}
                  {i.risk === "ask" && <span className="ml-1 text-fg-muted">· asks first</span>}
                </li>
              ))}
            </ul>
          )}
          {Object.keys(r.trace ?? {}).length > 0 && (
            <details>
              <summary className="cursor-pointer text-fg-secondary">Each node</summary>
              <ul className="mt-1 flex flex-col gap-0.5 font-mono text-[11.5px]">
                {Object.entries(r.trace ?? {}).map(([id, t]) => (
                  <li key={id}>
                    {id}: {String((t as { status?: string }).status ?? "")}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {(r.log ?? []).length > 0 && (
            <pre className="max-h-[200px] overflow-auto whitespace-pre-wrap font-mono text-[11.5px] text-fg-secondary">
              {(r.log ?? []).join("\n")}
            </pre>
          )}
        </div>
      )}
    </aside>
  );
}
