"use client";

/** The settings of an automation's own nodes on the canvas: an action (its params from the actions catalog, drawn
 * by SchemaForm) and a decide node (its question and options). */
import { useState } from "react";

import { SchemaForm, type Variable } from "@/components/forms/schema-form";
import type { JsonSchema } from "@/components/forms/schema-model";
import { Badge } from "@/components/ui/badge";
import { Field, Input, Select, Textarea } from "@/components/ui/field";
import { decideOptions, effectBadge, type ActionDef } from "@/components/workflows/workflow-model";

export function ActionSettings({
  config: c,
  set,
  actions,
  variables,
  nodeId,
}: {
  config: Record<string, unknown>;
  set: (patch: Record<string, unknown>) => void;
  actions: ActionDef[];
  variables?: Variable[];
  nodeId: string;
}) {
  const a = actions.find((x) => x.name === c.action);
  const badge = effectBadge(a?.effect);
  return (
    <>
      <Field label="Action" hint={a?.description}>
        {({ id, describedBy }) => (
          <Select
            id={id}
            aria-describedby={describedBy}
            value={String(c.action ?? "")}
            // another action's params mean nothing to this one
            onChange={(e) => set({ action: e.target.value || undefined, params: {} })}
            options={[
              { value: "", label: "Choose an action…" },
              ...actions.map((x) => ({ value: x.name, label: `${x.group} · ${x.label}` })),
            ]}
          />
        )}
      </Field>
      {a && (
        <>
          <Badge tone={badge.tone} className="self-start">
            {badge.label}
          </Badge>
          <SchemaForm
            idPrefix={`${nodeId}-param`}
            schema={a.params as JsonSchema}
            required={a.required}
            value={(c.params as Record<string, unknown> | undefined) ?? {}}
            onChange={(params) => set({ params })}
            variables={variables}
          />
        </>
      )}
      <Field
        label="Wait first"
        optional
        hint="Like 30m, 2h or 1d: it waits, then acts. It passes nothing on, so nothing connects after it."
      >
        {({ id, describedBy }) => (
          <Input
            id={id}
            aria-describedby={describedBy}
            mono
            value={String(c.delay ?? "")}
            placeholder="30m"
            onChange={(e) => set({ delay: e.target.value.trim() || undefined })}
          />
        )}
      </Field>
    </>
  );
}

/** Options written one a line as name: what it means. */
const optionLines = (c: Record<string, unknown>) =>
  decideOptions(c)
    .map((k) => `${k}: ${String((c.options as Record<string, unknown>)[k] ?? "")}`)
    .join("\n");

export function DecideSettings({
  config: c,
  set,
}: {
  config: Record<string, unknown>;
  set: (patch: Record<string, unknown>) => void;
}) {
  const [lines, setLines] = useState(() => optionLines(c));
  return (
    <>
      <Field label="Question" hint="What the decision model answers, e.g. Does this email need a reply today?">
        {({ id, describedBy }) => (
          <Textarea
            id={id}
            aria-describedby={describedBy}
            rows={3}
            value={String(c.question ?? "")}
            onChange={(e) => set({ question: e.target.value })}
          />
        )}
      </Field>
      <Field label="Options" hint="One a line, name: what it means. Each is an output port, and unsure is one too.">
        {({ id, describedBy }) => (
          <Textarea
            id={id}
            aria-describedby={describedBy}
            mono
            rows={4}
            value={lines}
            placeholder={"yes: it needs a reply today\nno: it can wait"}
            onChange={(e) => {
              setLines(e.target.value);
              set({
                options: Object.fromEntries(
                  e.target.value
                    .split("\n")
                    .map((l) => l.trim())
                    .filter(Boolean)
                    .map((l) => {
                      const at = l.indexOf(":");
                      return at < 0 ? [l, ""] : [l.slice(0, at).trim(), l.slice(at + 1).trim()];
                    }),
                ),
              });
            }}
          />
        )}
      </Field>
      <Field
        label="What it reads"
        optional
        hint="A template; empty reads what happened, its outside text marked as data"
      >
        {({ id, describedBy }) => (
          <Textarea
            id={id}
            aria-describedby={describedBy}
            mono
            rows={2}
            value={String(c.input ?? "")}
            placeholder="{{ event.summary }}"
            onChange={(e) => set({ input: e.target.value || undefined })}
          />
        )}
      </Field>
      <Field label="Sure enough at" optional hint="From 0 to 1; below it the run goes on along unsure">
        {({ id, describedBy }) => (
          <Input
            id={id}
            aria-describedby={describedBy}
            type="number"
            step={0.05}
            min={0}
            max={1}
            value={c.min_confidence == null ? "" : String(c.min_confidence)}
            onChange={(e) => set({ min_confidence: e.target.value === "" ? undefined : Number(e.target.value) })}
          />
        )}
      </Field>
    </>
  );
}
