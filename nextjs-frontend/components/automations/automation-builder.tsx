"use client";

/**
 * The builder: When (triggers and the join line), If (conditions on the run) and Then (actions in order), read top to
 * bottom, with "Describe it in words" on a new one, a test panel, and Save and turn on. Ctrl/Cmd+Enter saves; Enter
 * on the last row adds the next.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FlaskConical, Plus, Sparkles, Workflow as WorkflowIcon } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState, type KeyboardEvent } from "react";

import { Automations } from "@/app/openapi-client";
import type { Automation } from "@/app/openapi-client/types.gen";
import { useActions, useKinds } from "@/components/automations/data";
import {
  CLOCK,
  draftProblems,
  fromApi,
  joinFor,
  newTrigger,
  stepId,
  suggestedName,
  toApi,
  type Draft,
  type FieldType,
  type Step,
} from "@/components/automations/model";
import { JoinLine, RowsEditor, StepCard, TriggerCard, type FieldInfo } from "@/components/automations/parts";
import { TestPanel } from "@/components/automations/test-panel";
import type { Variable } from "@/components/forms/schema-form";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { Menu, MenuContent, MenuItem, MenuLabel, MenuTrigger } from "@/components/ui/menu";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";

const BLANK: Draft = {
  name: "",
  triggers: [],
  join: { mode: "any" },
  if: null,
  steps: [],
  graph: { nodes: [], edges: [] },
};

/** Where an API problem belongs: a trigger (by index), an action (by id), or the automation. */
export function problemAt(msg: string, d: Draft): string {
  const t = /^trigger (\w+)/.exec(msg)?.[1];
  const k = d.triggers.findIndex((x) => x.as === t);
  if (k >= 0) return `trigger.${k}`;
  const n = /(?:action node|decide) (\w+)/.exec(msg)?.[1];
  if (n && d.steps?.some((s) => s.id === n)) return `step.${n}`;
  return "";
}

const TYPES: Record<string, FieldType> = {
  string: "text",
  number: "number",
  integer: "number",
  array: "list",
  boolean: "bool",
  time: "time",
  datetime: "time",
};

export type Hook = { as: string; url: string; secret: string };

export function AutomationBuilder({
  automation,
  onSaved,
}: {
  automation?: Automation;
  /** After a save: its id, and any new webhook addresses with their secrets (shown once). */
  onSaved?: (id: number, hooks: Hook[]) => void;
}) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const actions = useActions();
  const kinds = useKinds();
  const acts = actions.data;
  const [draft, setDraft] = useState<Draft>(() => (automation ? fromApi(automation as never, acts) : BLANK));
  const [policy, setPolicy] = useState<Record<string, string>>(() => ({ ...(automation?.policy ?? {}) }));
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState<{ at: string; message: string } | null>(null);
  const [notes, setNotes] = useState<string[]>([]);
  const [words, setWords] = useState("");
  const saved = automation?.id;

  // once the catalog is in, a saved graph is read as steps against it (what passes nothing on hangs as a leaf)
  useEffect(() => {
    if (automation && acts)
      setDraft((d) => (d.steps === null || !d.steps.length ? fromApi(automation as never, acts) : d));
  }, [automation, acts]);

  const kindLabel = (k: string) => kinds.data?.find((x) => x.name === k)?.label ?? k;
  const actionLabel = (a: string) => acts?.find((x) => x.name === a)?.label ?? a;
  const guess = suggestedName(draft, { kind: kindLabel, action: actionLabel });
  const problems = { ...draftProblems(draft, acts), ...(error ? { [error.at]: error.message } : {}) };

  // what templates and conditions can read, from the draft's triggers
  const catalogKey = JSON.stringify(draft.triggers.map((t) => [t.as, t.on, t.kinds]));
  const vars = useQuery({
    queryKey: ["automation-variables", saved ?? null, catalogKey],
    queryFn: () =>
      data(
        Automations.variableCatalog({
          client,
          body: { draft: { triggers: draft.triggers, ...(saved ? { id: saved } : {}) } as never },
        }),
      ),
    enabled: draft.triggers.length > 0,
    retry: false,
    staleTime: 60_000,
  });
  const variables: Variable[] = useMemo(
    () => (vars.data?.variables ?? []).map((v) => ({ path: v.path, label: v.label, group: v.group })),
    [vars.data],
  );
  const ifFields: FieldInfo[] = (vars.data?.variables ?? []).map((v) => ({
    path: v.path,
    label: v.label,
    type: TYPES[v.type] ?? "any",
  }));
  const stepVariables = (k: number): Variable[] => [
    ...variables,
    ...(draft.steps ?? []).slice(0, k).map((s) => ({
      path: `steps.${s.id}`,
      label: `What ${actionLabel(s.action)} gave back`,
      group: "Steps",
    })),
  ];

  const set = (patch: Partial<Draft>) => {
    setError(null);
    setDraft((d) => ({ ...d, ...patch }));
  };
  const setSteps = (steps: Step[]) => set({ steps });

  const describe = useMutation({
    mutationFn: (text: string) => data(Automations.draftAutomation({ client, body: { text } })),
    onSuccess: (r) => {
      const d = fromApi(r.draft as never, acts);
      setDraft({ ...d, steps: d.steps ?? [] });
      setNotes([...(r.dropped ?? []), ...(r.unmapped ?? []).map((u) => `Not done: ${u}`)]);
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t draft it", body: e.message }),
  });

  const save = useMutation({
    mutationFn: async (turnOn: boolean) => {
      const body = toApi({ ...draft, name: draft.name.trim() || guess }, acts);
      if (!saved) {
        const r = await data(Automations.createAutomation({ client, body: { ...body, enabled: turnOn } as never }));
        return { id: r.id, hooks: r.hooks ?? [] };
      }
      const r = await data(Automations.updateAutomation({ client, path: { aid: saved }, body: body as never }));
      if (turnOn && !automation?.enabled)
        await data(Automations.enableAutomation({ client, path: { aid: saved }, body: { on: true } }));
      return { id: saved, hooks: r.hooks ?? [] };
    },
    onSuccess: (r, turnOn) => {
      void qc.invalidateQueries({ queryKey: ["automations"] });
      void qc.invalidateQueries({ queryKey: ["automation", r.id] });
      toast({
        tone: "green",
        title: turnOn ? "Saved and on" : "Saved",
        body: turnOn ? "It acts on what happens from now on." : saved ? undefined : "It’s off until you turn it on.",
      });
      onSaved?.(r.id, r.hooks as Hook[]);
    },
    onError: (e: Error) => setError({ at: problemAt(e.message, draft), message: e.message }),
  });

  const policyFor = useMutation({
    mutationFn: ({ node, auto }: { node: string; auto: boolean }) =>
      data(Automations.setPolicy({ client, path: { aid: saved! }, body: { node, value: auto ? "auto" : "ask" } })),
    onSuccess: (r) => {
      setPolicy(r as Record<string, string>);
      void qc.invalidateQueries({ queryKey: ["automation", saved] });
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t change it", body: e.message }),
  });

  const blocked = Object.entries(draftProblems(draft, acts))[0];
  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && !blocked && !save.isPending) {
      e.preventDefault();
      save.mutate(false);
    }
  };
  const savedNodes = new Set(
    ((automation?.graph as { nodes?: { id: string }[] } | null)?.nodes ?? []).map((n) => n.id),
  );
  const groups = [...new Set((kinds.data ?? []).filter((k) => !k.noisy).map((k) => k.source))];
  const actionGroups = [...new Set((acts ?? []).map((a) => a.group))];

  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_340px]" onKeyDown={onKeyDown}>
      <div className="flex flex-col gap-6">
        {!saved && (
          <section className="flex flex-col gap-1.5">
            <label htmlFor="describe" className="text-[13px] font-bold text-fg-strong">
              Describe it in words
            </label>
            <div className="flex gap-2">
              <Input
                id="describe"
                autoFocus
                value={words}
                placeholder="When an email from the school arrives, tell me"
                onChange={(e) => setWords(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.metaKey && !e.ctrlKey && words.trim()) {
                    e.preventDefault();
                    describe.mutate(words.trim());
                  }
                }}
              />
              <Button
                variant="secondary"
                icon={<Sparkles />}
                disabled={!words.trim() || describe.isPending}
                onClick={() => describe.mutate(words.trim())}
              >
                {describe.isPending ? "Drafting…" : "Draft it"}
              </Button>
            </div>
            <p className="text-[12.5px] text-fg-muted">
              The model fills the builder in; nothing is saved until you save.
            </p>
            {notes.length > 0 && (
              <ul aria-label="What the draft left out" className="list-disc pl-5 text-[12.5px] text-fg-secondary">
                {notes.map((n) => (
                  <li key={n}>{n}</li>
                ))}
              </ul>
            )}
          </section>
        )}

        <Input
          aria-label="Name"
          className="text-[16px] font-bold"
          value={draft.name}
          placeholder={guess}
          onChange={(e) => set({ name: e.target.value })}
        />

        <section aria-labelledby="when" className="flex flex-col gap-3">
          <h2 id="when" className="text-[16px] font-bold text-fg">
            When
          </h2>
          {problems.triggers && <p className="text-[12.5px] text-red-dark">{problems.triggers}</p>}
          {draft.triggers.map((t, k) => (
            <TriggerCard
              key={t.as}
              trigger={t}
              index={k}
              kinds={kinds.data ?? []}
              problem={problems[`trigger.${k}`]}
              onChange={(x) => set({ triggers: draft.triggers.map((y, i) => (i === k ? x : y)) })}
              onRemove={() => {
                const triggers = draft.triggers.filter((_, i) => i !== k);
                set({
                  triggers,
                  join: triggers.length > 1 ? joinFor(draft.join.mode, triggers, draft.join) : { mode: "any" },
                });
              }}
            />
          ))}
          {draft.triggers.length > 1 && (
            <JoinLine join={draft.join} triggers={draft.triggers} onChange={(join) => set({ join })} />
          )}
          <Menu>
            <MenuTrigger asChild>
              <Button size="sm" variant="secondary" icon={<Plus />} className="self-start">
                Add a trigger
              </Button>
            </MenuTrigger>
            <MenuContent align="start" className="max-h-[420px] w-[300px] overflow-y-auto">
              {groups.map((g) => (
                <div key={g}>
                  <MenuLabel>{g}</MenuLabel>
                  {(kinds.data ?? [])
                    .filter((k) => k.source === g && !k.noisy)
                    .map((k) => (
                      <MenuItem
                        key={k.name}
                        onSelect={() =>
                          set({
                            triggers: [
                              ...draft.triggers,
                              newTrigger(
                                k.name,
                                draft.triggers.map((x) => x.as),
                              ),
                            ],
                          })
                        }
                      >
                        {k.label}
                      </MenuItem>
                    ))}
                </div>
              ))}
              <MenuLabel>Time and calls</MenuLabel>
              {CLOCK.map((c) => (
                <MenuItem
                  key={c.on}
                  onSelect={() =>
                    set({
                      triggers: [
                        ...draft.triggers,
                        newTrigger(
                          c.on,
                          draft.triggers.map((x) => x.as),
                          Intl.DateTimeFormat().resolvedOptions().timeZone,
                        ),
                      ],
                    })
                  }
                >
                  {c.label}
                </MenuItem>
              ))}
            </MenuContent>
          </Menu>
        </section>

        <section aria-labelledby="if" className="flex flex-col gap-3">
          <h2 id="if" className="text-[16px] font-bold text-fg">
            If <span className="text-[13px] font-normal text-fg-muted">optional</span>
          </h2>
          <RowsEditor
            idPrefix="if"
            value={draft.if ?? undefined}
            onChange={(tree) => set({ if: tree ?? null })}
            fields={ifFields}
            empty="It always goes on to Then."
          />
        </section>

        <section aria-labelledby="then" className="flex flex-col gap-3">
          <h2 id="then" className="text-[16px] font-bold text-fg">
            Then
          </h2>
          {draft.steps === null ? (
            <div className="flex flex-col items-start gap-2 rounded-md border border-border bg-surface px-4 py-3 text-[13.5px]">
              <p>This automation branches; edit it in the canvas.</p>
              {automation?.workflow != null && (
                <Button asChild size="sm" variant="secondary" icon={<WorkflowIcon />}>
                  <Link href={`/workflows/${automation.workflow}`}>Open in canvas</Link>
                </Button>
              )}
            </div>
          ) : (
            <>
              {problems.steps && <p className="text-[12.5px] text-red-dark">{problems.steps}</p>}
              {draft.steps.map((s, k) => (
                <StepCard
                  key={s.id}
                  step={s}
                  index={k}
                  count={draft.steps!.length}
                  actions={acts ?? []}
                  variables={stepVariables(k)}
                  policy={policy}
                  saved={Boolean(saved) && savedNodes.has(s.id)}
                  problem={problems[`step.${s.id}`]}
                  onChange={(x) => setSteps(draft.steps!.map((y) => (y.id === s.id ? x : y)))}
                  onRemove={() => setSteps(draft.steps!.filter((y) => y.id !== s.id))}
                  onMove={(by) => {
                    const list = [...draft.steps!];
                    const [x] = list.splice(k, 1);
                    list.splice(k + by, 0, x);
                    setSteps(list);
                  }}
                  onPolicy={(auto) => policyFor.mutate({ node: s.id, auto })}
                />
              ))}
              <Menu>
                <MenuTrigger asChild>
                  <Button size="sm" variant="secondary" icon={<Plus />} className="self-start">
                    Add an action
                  </Button>
                </MenuTrigger>
                <MenuContent align="start" className="max-h-[420px] w-[300px] overflow-y-auto">
                  {actionGroups.map((g) => (
                    <div key={g}>
                      <MenuLabel>{g}</MenuLabel>
                      {(acts ?? [])
                        .filter((a) => a.group === g)
                        .map((a) => (
                          <MenuItem
                            key={a.name}
                            onSelect={() =>
                              setSteps([
                                ...(draft.steps ?? []),
                                { id: stepId(draft.steps ?? [], a.name), action: a.name, params: {} },
                              ])
                            }
                          >
                            {a.label}
                          </MenuItem>
                        ))}
                    </div>
                  ))}
                </MenuContent>
              </Menu>
              {automation?.workflow != null && draft.steps.length > 0 && (
                <Link
                  href={`/workflows/${automation.workflow}`}
                  className="text-[12.5px] text-fg-accent hover:underline"
                >
                  Open in canvas, to branch on a condition or the model’s pick
                </Link>
              )}
            </>
          )}
        </section>

        {problems[""] && (
          <p
            role="alert"
            className="rounded-sm border border-red-border bg-red-surface px-3 py-2 text-[13px] text-red-dark"
          >
            {problems[""]}
          </p>
        )}
        <div className="flex flex-wrap items-center gap-2">
          {!automation?.enabled && (
            <Button
              variant="primary"
              disabled={Boolean(blocked) || save.isPending}
              disabledReason={blocked?.[1]}
              onClick={() => save.mutate(true)}
            >
              Save and turn on
            </Button>
          )}
          <Button
            variant={automation?.enabled ? "primary" : "secondary"}
            disabled={Boolean(blocked) || save.isPending}
            disabledReason={blocked?.[1]}
            onClick={() => save.mutate(false)}
          >
            {save.isPending ? "Saving…" : "Save"}
          </Button>
          <Button variant="ghost" icon={<FlaskConical />} onClick={() => setTesting((v) => !v)}>
            Test
          </Button>
          <span className="text-[12px] text-fg-muted">Ctrl or ⌘ + Enter saves.</span>
        </div>
      </div>
      {testing && <TestPanel draft={draft} actions={acts} kinds={kinds.data ?? []} onClose={() => setTesting(false)} />}
    </div>
  );
}
