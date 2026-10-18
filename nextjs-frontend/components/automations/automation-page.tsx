"use client";

/** One automation: its name, on or off, Run now, and tabs for the builder, its runs, what's coming up, its variables
 * and its costs; a new one starts from the builder alone. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Play, Zap } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Automations } from "@/app/openapi-client";
import { AutomationBuilder, type Hook } from "@/components/automations/automation-builder";
import { CostsTab, RunsTab, UpcomingTab, VariablesTab } from "@/components/automations/detail-tabs";
import { runStatus } from "@/components/automations/model";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/field";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { Tabs } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";

const HOOKS = (id: number) => `automation-hooks-${id}`;

function Crumbs({ name }: { name: string }) {
  return (
    <nav aria-label="Breadcrumb" className="text-[12px] font-medium text-fg-muted">
      <Link href="/automations" className="hover:text-fg hover:underline">
        Automations
      </Link>{" "}
      › {name}
    </nav>
  );
}

export function NewAutomationPage() {
  const router = useRouter();
  return (
    <div className="flex flex-col gap-4 px-4 pb-10 pt-[18px] md:px-6">
      <Crumbs name="New automation" />
      <h1 className="text-[24px] font-bold leading-tight text-fg">New automation</h1>
      <AutomationBuilder
        onSaved={(id, hooks) => {
          // a webhook's secret shows once: carry it to the automation's page
          if (hooks.length)
            try {
              sessionStorage.setItem(HOOKS(id), JSON.stringify(hooks));
            } catch {
              // no storage: the page says to look at the trigger again
            }
          router.push(`/automations/${id}`);
        }}
      />
    </div>
  );
}

export const TABS = ["edit", "runs", "upcoming", "variables", "costs"] as const;
export type Tab = (typeof TABS)[number];

export function AutomationPage({ id, tab: first }: { id: number; tab?: string }) {
  const [tab, setTab] = useState<Tab>(TABS.includes(first as Tab) ? (first as Tab) : "edit");
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({
    queryKey: ["automation", id],
    queryFn: () => data(Automations.getAutomation({ client, path: { aid: id } })),
  });
  const [hooks, setHooks] = useState<Hook[]>([]);
  useEffect(() => {
    try {
      const got = sessionStorage.getItem(HOOKS(id));
      if (got) {
        setHooks(JSON.parse(got) as Hook[]);
        sessionStorage.removeItem(HOOKS(id));
      }
    } catch {
      // nothing carried over
    }
  }, [id]);
  const a = q.data;
  const toggle = useMutation({
    mutationFn: (on: boolean) => data(Automations.enableAutomation({ client, path: { aid: id }, body: { on } })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["automation", id] });
      void qc.invalidateQueries({ queryKey: ["automations"] });
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t switch it", body: e.message }),
  });
  const run = useMutation({
    mutationFn: () => data(Automations.runAutomation({ client, path: { aid: id }, body: {} })),
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: ["automation-runs", id] });
      setTab("runs");
      toast(
        r.run
          ? { tone: "green", title: "It ran", body: runStatus(r.status).label }
          : { tone: "gate", title: "It didn’t run", body: r.why ?? undefined },
      );
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t run it", body: e.message }),
  });

  if (q.isLoading)
    return (
      <div className="flex flex-col gap-4 p-6" aria-busy="true" aria-label="Loading automation">
        <Skeleton className="h-7 w-72" />
        <Skeleton className="h-[420px] w-full rounded-md" />
      </div>
    );
  if (q.error || !a) {
    const e = q.error as ApiError | null;
    return (
      <EmptyState
        tone={e?.status === 404 ? "neutral" : "error"}
        icon={<Zap />}
        title={e?.status === 404 ? "This automation doesn’t exist" : "Couldn’t load the automation"}
        actions={
          <Button asChild variant="secondary">
            <Link href="/automations">All automations</Link>
          </Button>
        }
      >
        {e?.message}
      </EmptyState>
    );
  }
  return (
    <div className="flex flex-col gap-4 px-4 pb-10 pt-[18px] md:px-6">
      <Crumbs name={a.name} />
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="min-w-0 flex-1 text-[24px] font-bold leading-tight text-fg">{a.name}</h1>
        <Switch
          checked={a.enabled}
          disabled={toggle.isPending}
          onCheckedChange={(on) => toggle.mutate(on)}
          label={a.enabled ? "On" : "Off"}
          aria-label={`${a.name} on`}
        />
        <Button size="sm" variant="secondary" icon={<Play />} disabled={run.isPending} onClick={() => run.mutate()}>
          Run now
        </Button>
      </div>
      {hooks.length > 0 && (
        <div className="flex flex-col gap-1 rounded-md border border-gold-border bg-gold-surface px-4 py-3 text-[13px]">
          <p className="font-bold">Its webhook addresses, shown this once: keep the secret to sign calls.</p>
          {hooks.map((h) => (
            <p key={h.as} className="break-all font-mono text-[12px]">
              {h.as}: {h.url} · secret {h.secret}
            </p>
          ))}
        </div>
      )}
      <Tabs
        aria-label="Automation"
        value={tab}
        onChange={(v) => setTab(v as Tab)}
        items={[
          { value: "edit", label: "Edit" },
          { value: "runs", label: "Runs" },
          { value: "upcoming", label: "Coming up" },
          { value: "variables", label: "Variables" },
          { value: "costs", label: "Costs" },
        ]}
      />
      {tab === "runs" ? (
        <RunsTab aid={a.id} />
      ) : tab === "upcoming" ? (
        <UpcomingTab aid={a.id} enabled={a.enabled} />
      ) : tab === "variables" ? (
        <VariablesTab aid={a.id} />
      ) : tab === "costs" ? (
        <CostsTab aid={a.id} />
      ) : (
        <AutomationBuilder
          key={`${a.id}-${a.updated_at}`}
          automation={a}
          onSaved={(_, got) => got.length && setHooks(got)}
        />
      )}
    </div>
  );
}
