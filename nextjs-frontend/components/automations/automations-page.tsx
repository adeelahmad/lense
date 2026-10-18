"use client";

/** Automations: each one's name, what sets it off, whether it is on, its last run, runs today, what waits for you,
 * what it cost this month and when it runs next; Run now, Duplicate and Delete (with Undo) on each. */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Ellipsis, Plus, Zap } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { Automations } from "@/app/openapi-client";
import type { Automation } from "@/app/openapi-client/types.gen";
import { AutomateTabs, RoutineLinks } from "@/components/automations/automate-tabs";
import { runStatus } from "@/components/automations/model";
import { useAutomations } from "@/components/automations/data";
import { CostCell, useBudgets, useCosts } from "@/components/costs/costs";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/field";
import { Menu, MenuContent, MenuItem, MenuSeparator, MenuTrigger } from "@/components/ui/menu";
import { DateTime, EmptyState, PageHeader, SkeletonRows } from "@/components/ui/states";
import { Table, Td, Th, THead, Tr } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";

export function AutomationsPage() {
  const client = useApiClient();
  const qc = useQueryClient();
  const router = useRouter();
  const toast = useToast();
  const list = useAutomations();
  const rows = list.data?.automations ?? [];
  const costs = useCosts(rows.map((a) => `automation:${a.id}`));
  const budgets = useBudgets();
  const refresh = () => qc.invalidateQueries({ queryKey: ["automations"] });

  const toggle = useMutation({
    mutationFn: ({ a, on }: { a: Automation; on: boolean }) =>
      data(Automations.enableAutomation({ client, path: { aid: a.id }, body: { on } })),
    onSuccess: () => void refresh(),
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t switch it", body: e.message }),
  });
  const run = useMutation({
    mutationFn: (a: Automation) => data(Automations.runAutomation({ client, path: { aid: a.id }, body: {} })),
    onSuccess: (r, a) => {
      void refresh();
      toast(
        r.run
          ? {
              tone: "green",
              title: `${a.name} ran`,
              body: runStatus(r.status).label,
              action: { label: "Open", onClick: () => router.push(`/automations/${a.id}?tab=runs`) },
            }
          : { tone: "gate", title: `${a.name} didn’t run`, body: r.why ?? undefined },
      );
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t run it", body: e.message }),
  });
  const duplicate = useMutation({
    mutationFn: async (a: Automation) => {
      const body = {
        name: `${a.name} (copy)`,
        // a copy's webhook gets an address of its own when it is saved
        triggers: a.triggers.map((t) => (t.on === "webhook" ? { as: t.as, on: t.on } : t)),
        join: a.join,
        if: a.if ?? null,
        graph: a.graph ?? undefined,
        workflow: a.graph ? undefined : a.workflow,
        spaces: a.spaces,
        limits: a.limits,
        enabled: false,
      };
      return data(Automations.createAutomation({ client, body: body as never }));
    },
    onSuccess: (r) => {
      void refresh();
      router.push(`/automations/${r.id}`);
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t duplicate it", body: e.message }),
  });
  const restore = useMutation({
    mutationFn: (a: Automation) => data(Automations.restoreAutomation({ client, path: { aid: a.id } })),
    onSuccess: () => void refresh(),
  });
  const remove = useMutation({
    mutationFn: (a: Automation) => data(Automations.deleteAutomation({ client, path: { aid: a.id } })),
    onSuccess: (_, a) => {
      void refresh();
      toast({
        title: `Deleted ${a.name}`,
        body: "Undo brings it back, switched off.",
        action: { label: "Undo", onClick: () => restore.mutate(a) },
      });
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t delete it", body: e.message }),
  });

  return (
    <div className="flex flex-col gap-4 px-4 pb-10 pt-[18px] md:px-6">
      <PageHeader
        title="Automations"
        meta={rows.length ? `${rows.length} · ${rows.filter((a) => a.enabled).length} on` : undefined}
        actions={
          <>
            <RoutineLinks primary={false} />
            <Button asChild variant="primary">
              <Link href="/automations/new">
                <Plus /> New automation
              </Link>
            </Button>
          </>
        }
      >
        <p className="mt-1 text-[13.5px] text-fg-secondary">
          When something happens, if a test passes, Lens does something for you. What reaches outside Lens asks you
          first, on the Approvals page.
        </p>
      </PageHeader>
      <AutomateTabs tab="automations" />
      {list.isLoading ? (
        <SkeletonRows rows={4} />
      ) : list.error ? (
        <EmptyState
          tone="error"
          icon={<Zap />}
          title="Couldn’t load automations"
          actions={<Button onClick={() => list.refetch()}>Try again</Button>}
        >
          {(list.error as Error).message}
        </EmptyState>
      ) : !rows.length ? (
        <EmptyState
          icon={<Zap />}
          title="No automations yet"
          actions={
            <Button asChild variant="primary">
              <Link href="/automations/new">
                <Plus /> New automation
              </Link>
            </Button>
          }
        >
          Tell Lens what to do when something happens: an email from the school arrives, a sensor raises an alert, or
          every weekday at seven. Describe it in words and Lens drafts it.
        </EmptyState>
      ) : (
        <div className="overflow-x-auto rounded-md border border-border">
          <Table aria-label="Automations">
            <THead className="border-t-0">
              <tr>
                <Th>Automation</Th>
                <Th>Last run</Th>
                <Th className="text-right">Today</Th>
                <Th className="text-right">Waiting</Th>
                <Th className="text-right">This month</Th>
                <Th>Next</Th>
                <Th>On</Th>
                <Th>
                  <span className="sr-only">Actions</span>
                </Th>
              </tr>
            </THead>
            <tbody>
              {rows.map((a) => {
                const st = a.last_status ? runStatus(a.last_status) : null;
                return (
                  <Tr key={a.id} className="h-[62px]">
                    <Td className="max-w-[460px]">
                      <div className="flex min-w-0 flex-col gap-0.5">
                        <Link
                          href={`/automations/${a.id}`}
                          className="font-semibold text-fg hover:text-fg-accent hover:underline"
                        >
                          {a.name}
                        </Link>
                        {a.reads && <span className="line-clamp-2 text-[12px] text-fg-muted">{a.reads}</span>}
                      </div>
                    </Td>
                    <Td className="whitespace-nowrap text-[13px]">
                      <div className="flex flex-col items-start gap-0.5">
                        {st ? <Badge tone={st.tone}>{st.label}</Badge> : <span className="text-fg-muted">Not yet</span>}
                        {a.last_run_at && <DateTime iso={a.last_run_at} className="text-[12px] text-fg-muted" />}
                      </div>
                    </Td>
                    <Td className="text-right text-[13px] tabular">{a.runs_today ?? 0}</Td>
                    <Td className="text-right text-[13px] tabular">
                      {a.waiting ? (
                        <Link href="/approvals" className="font-semibold text-fg-accent hover:underline">
                          {a.waiting}
                        </Link>
                      ) : (
                        0
                      )}
                    </Td>
                    <Td className="whitespace-nowrap text-right text-[13px] text-fg-secondary">
                      <CostCell resource={`automation:${a.id}`} costs={costs.data?.costs} budgets={budgets} />
                    </Td>
                    <Td className="whitespace-nowrap text-[13px] text-fg-secondary">
                      {a.enabled && a.next_at ? <DateTime iso={a.next_at} /> : "—"}
                    </Td>
                    <Td>
                      <Switch
                        aria-label={`${a.name} on`}
                        checked={a.enabled}
                        disabled={toggle.isPending && toggle.variables?.a.id === a.id}
                        onCheckedChange={(on) => toggle.mutate({ a, on })}
                      />
                    </Td>
                    <Td>
                      <Menu>
                        <MenuTrigger asChild>
                          <button
                            type="button"
                            aria-label={`More for ${a.name}`}
                            className="grid size-8 place-items-center rounded-full text-fg-muted hover:bg-surface-neutral hover:text-fg"
                          >
                            <Ellipsis aria-hidden className="size-[18px]" />
                          </button>
                        </MenuTrigger>
                        <MenuContent align="end">
                          <MenuItem onSelect={() => run.mutate(a)}>Run now</MenuItem>
                          <MenuItem onSelect={() => duplicate.mutate(a)}>Duplicate</MenuItem>
                          <MenuSeparator />
                          <MenuItem danger onSelect={() => remove.mutate(a)}>
                            Delete
                          </MenuItem>
                        </MenuContent>
                      </Menu>
                    </Td>
                  </Tr>
                );
              })}
            </tbody>
          </Table>
        </div>
      )}
    </div>
  );
}
