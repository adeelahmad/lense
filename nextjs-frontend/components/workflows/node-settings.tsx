"use client";

import { useState } from "react";

import type { FieldDef, TemplateSummary } from "@/app/openapi-client/types.gen";
import { OPS, infoFor, type Scope, type WfNode } from "@/components/workflows/workflow-model";
import { Checkbox, Field, Input, Select, Textarea } from "@/components/ui/field";

export type NsField = FieldDef & { namespace: string };

/** A list edited as lines of text; the lines are kept as typed until the node changes. */
function Lines({
  label,
  hint,
  value,
  parse,
  show,
  placeholder,
}: {
  label: string;
  hint: string;
  value: unknown[];
  parse: (lines: string[]) => void;
  show: (v: unknown) => string;
  placeholder?: string;
}) {
  const [text, setText] = useState(() => value.map(show).join("\n"));
  return (
    <Field label={label} optional hint={hint}>
      {({ id, describedBy }) => (
        <Textarea
          id={id}
          aria-describedby={describedBy}
          rows={4}
          className="font-mono text-[12.5px]"
          value={text}
          placeholder={placeholder}
          onChange={(e) => {
            setText(e.target.value);
            parse(
              e.target.value
                .split("\n")
                .map((l) => l.trim())
                .filter(Boolean),
            );
          }}
        />
      )}
    </Field>
  );
}

const typesText = (v: unknown) => ((v as string[] | undefined) ?? []).join(", ");
const typesOf = (s: string) =>
  s
    .split(/[,\s]+/)
    .map((x) => x.trim().toUpperCase())
    .filter(Boolean);

/** A typed value: numbers and true/false as such, anything else as text. */
function parseValue(s: string): unknown {
  const t = s.trim();
  if (t === "") return undefined;
  if (t === "true" || t === "false") return t === "true";
  if (!Number.isNaN(Number(t))) return Number(t);
  return s;
}

/** A number typed in a box: empty is unset. */
const num = (s: string) => (s.trim() === "" ? undefined : Number(s));

/** The selected node's settings, what it takes in and what it passes on. */
export function NodeSettings({
  node,
  onChange,
  templates,
  fields,
  readOnly,
  problem,
  scope = "recording",
}: {
  node: WfNode;
  onChange: (n: WfNode) => void;
  templates: TemplateSummary[];
  fields: NsField[];
  readOnly?: boolean;
  problem?: string;
  scope?: Scope;
}) {
  const info = infoFor(node.type, scope);
  const c = node.config;
  const set = (patch: Record<string, unknown>) => {
    const config = { ...c, ...patch };
    for (const k of Object.keys(config)) if (config[k] === undefined || config[k] === "") delete config[k];
    onChange({ ...node, config });
  };
  return (
    <div className="flex flex-col gap-3" key={node.id}>
      <div>
        <h2 className="text-[15px] font-bold text-fg">{node.label || info?.label || node.type}</h2>
        <p className="mt-0.5 text-[12.5px] text-fg-secondary">{info?.describe}</p>
        <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[12px]">
          <dt className="text-fg-muted">Takes</dt>
          <dd className="font-mono text-fg">{info?.takes}</dd>
          <dt className="text-fg-muted">Passes on</dt>
          <dd className="font-mono text-fg">
            {node.type === "output" && c.key ? `outputs.${String(c.key)}` : info?.gives}
          </dd>
        </dl>
      </div>
      {problem && (
        <p
          role="alert"
          className="rounded-sm border border-red-border bg-red-surface px-3 py-2 text-[12.5px] text-red-dark"
        >
          {problem}
        </p>
      )}
      <fieldset disabled={readOnly} className="flex flex-col gap-3">
        {node.type !== "input" && (
          <Field label="Label" optional hint="Shown on the canvas and in the run’s log">
            {({ id, describedBy }) => (
              <Input
                id={id}
                aria-describedby={describedBy}
                value={node.label ?? ""}
                placeholder={info?.label}
                onChange={(e) => onChange({ ...node, label: e.target.value })}
              />
            )}
          </Field>
        )}

        {node.type === "llm" && (
          <>
            <Field
              label="Prompt template"
              hint="What came in is {{ input }}; the recording is there as in any template"
            >
              {({ id, describedBy }) => (
                <Select
                  id={id}
                  aria-describedby={describedBy}
                  value={c.template == null ? "" : String(c.template)}
                  onChange={(e) => set({ template: e.target.value ? Number(e.target.value) : undefined })}
                  options={[
                    { value: "", label: "Choose a template…" },
                    ...templates
                      .filter((t) => t.kind === "prompt")
                      .map((t) => ({ value: String(t.id), label: `${t.name} · v${t.current}` })),
                  ]}
                />
              )}
            </Field>
            <Field label="Model" optional hint="Leave empty for the model in Settings → LLM">
              {({ id, describedBy }) => (
                <Input
                  id={id}
                  aria-describedby={describedBy}
                  mono
                  value={String(c.model ?? "")}
                  onChange={(e) => set({ model: e.target.value })}
                />
              )}
            </Field>
          </>
        )}

        {node.type === "pick" && (
          <Field label="Path" hint="Dots between keys, numbers for list items: action_items.0.text">
            {({ id, describedBy }) => (
              <Input
                id={id}
                aria-describedby={describedBy}
                mono
                value={String(c.path ?? "")}
                placeholder="tldr"
                onChange={(e) => set({ path: e.target.value })}
              />
            )}
          </Field>
        )}

        {(node.type === "condition" || node.type === "filter") && (
          <>
            <Field
              label="Path"
              optional
              hint={
                node.type === "filter"
                  ? "Tested in each item of the list, e.g. verdict.same or verdict.confidence"
                  : "Test a part of what came in, or leave empty to test all of it"
              }
            >
              {({ id, describedBy }) => (
                <Input
                  id={id}
                  aria-describedby={describedBy}
                  mono
                  value={String(c.path ?? "")}
                  placeholder={node.type === "filter" ? "verdict.same" : "summary.importance"}
                  onChange={(e) => set({ path: e.target.value })}
                />
              )}
            </Field>
            <Field label="Test">
              {({ id }) => (
                <Select
                  id={id}
                  value={String(c.op ?? "exists")}
                  onChange={(e) => set({ op: e.target.value })}
                  options={Object.entries(OPS).map(([value, label]) => ({ value, label }))}
                />
              )}
            </Field>
            {c.op !== "exists" && c.op !== "empty" && (
              <Field label="Value" hint="Numbers and true/false are compared as such">
                {({ id, describedBy }) => (
                  <Input
                    id={id}
                    aria-describedby={describedBy}
                    mono
                    defaultValue={c.value == null ? "" : String(c.value)}
                    onChange={(e) => set({ value: parseValue(e.target.value) })}
                  />
                )}
              </Field>
            )}
          </>
        )}

        {node.type === "extract_rules" && (
          <>
            <Checkbox
              checked={c.builtin !== false}
              onCheckedChange={(v) => set({ builtin: v })}
              label="Built-in extractor (what analyze finds today)"
              disabled={readOnly}
            />
            <Lines
              label="Terms"
              hint="One per line, as Name|TYPE: always found, wherever they’re said"
              placeholder={"Acme Corp|ORG\nProject Falcon|PRODUCT"}
              value={(c.terms as string[] | undefined) ?? []}
              show={(v) => String(v)}
              parse={(lines) => set({ terms: lines.length ? lines : undefined })}
            />
            <Lines
              label="Patterns"
              hint="One per line, as TYPE: regular expression"
              placeholder={"TICKET: [A-Z]+-\\d+\nEMAIL: [\\w.+-]+@[\\w-]+\\.[\\w.]+"}
              value={(c.patterns as { type: string; pattern: string }[] | undefined) ?? []}
              show={(v) => `${(v as { type: string }).type}: ${(v as { pattern: string }).pattern}`}
              parse={(lines) =>
                set({
                  patterns: lines.length
                    ? lines.map((l) => {
                        const i = l.indexOf(":");
                        return i < 0
                          ? { type: "", pattern: l }
                          : { type: l.slice(0, i).trim().toUpperCase(), pattern: l.slice(i + 1).trim() };
                      })
                    : undefined,
                })
              }
            />
            <Field label="Keep only these types" optional hint="e.g. PERSON, ORG; empty keeps all">
              {({ id, describedBy }) => (
                <Input
                  id={id}
                  aria-describedby={describedBy}
                  mono
                  defaultValue={typesText(c.types)}
                  onChange={(e) => set({ types: typesOf(e.target.value).length ? typesOf(e.target.value) : undefined })}
                />
              )}
            </Field>
          </>
        )}

        {node.type === "extract_llm" && (
          <>
            <Field label="Types" optional hint="What to look for, e.g. PERSON, ORG, PRODUCT; empty for all">
              {({ id, describedBy }) => (
                <Input
                  id={id}
                  aria-describedby={describedBy}
                  mono
                  defaultValue={typesText(c.types)}
                  onChange={(e) => set({ types: typesOf(e.target.value).length ? typesOf(e.target.value) : undefined })}
                />
              )}
            </Field>
            <Field label="Instructions" optional hint="Anything the model should know, e.g. what counts as a product">
              {({ id, describedBy }) => (
                <Textarea
                  id={id}
                  aria-describedby={describedBy}
                  rows={3}
                  value={String(c.instructions ?? "")}
                  onChange={(e) => set({ instructions: e.target.value })}
                />
              )}
            </Field>
            <Field label="Model" optional hint="Leave empty for the model in Settings → LLM">
              {({ id, describedBy }) => (
                <Input
                  id={id}
                  aria-describedby={describedBy}
                  mono
                  value={String(c.model ?? "")}
                  onChange={(e) => set({ model: e.target.value })}
                />
              )}
            </Field>
          </>
        )}

        {node.type === "output" && (
          <Field label="Output name" hint="Saved on the recording as outputs.<name>; lowercase letters, digits and _">
            {({ id, describedBy }) => (
              <Input
                id={id}
                aria-describedby={describedBy}
                mono
                value={String(c.key ?? "")}
                placeholder="meeting_notes"
                onChange={(e) => onChange({ ...node, config: { ...c, key: e.target.value } })}
              />
            )}
          </Field>
        )}

        {node.type === "field" && (
          <Field
            label="Custom field"
            hint={
              fields.length
                ? "Set on recordings in the field’s namespace; elsewhere the node fails and says why"
                : "No custom fields yet: add one in a namespace’s settings"
            }
          >
            {({ id, describedBy }) => (
              <Select
                id={id}
                aria-describedby={describedBy}
                value={c.field == null ? "" : String(c.field)}
                onChange={(e) => set({ field: e.target.value ? Number(e.target.value) : undefined })}
                options={[
                  { value: "", label: "Choose a field…" },
                  ...fields
                    .filter((f) => f.target === "resource")
                    .map((f) => ({ value: String(f.id), label: `${f.namespace} · ${f.label} (${f.type})` })),
                ]}
              />
            )}
          </Field>
        )}

        {node.type === "candidates" && (
          <>
            <Field label="Look for">
              {({ id }) => (
                <Select
                  id={id}
                  value={String(c.kind ?? "merge")}
                  onChange={(e) => set({ kind: e.target.value })}
                  options={[
                    { value: "merge", label: "Look-alikes in a namespace (to merge)" },
                    { value: "link", label: "Look-alikes across shared namespaces (to link)" },
                  ]}
                />
              )}
            </Field>
            <Field label="At least this sure" hint="0 to 1: how sure the rules must be that a pair is one thing">
              {({ id, describedBy }) => (
                <Input
                  id={id}
                  aria-describedby={describedBy}
                  type="number"
                  min={0}
                  max={1}
                  step={0.05}
                  value={c.min_confidence == null ? "" : String(c.min_confidence)}
                  onChange={(e) => set({ min_confidence: num(e.target.value) })}
                />
              )}
            </Field>
            <Field label="At most" hint="Pairs a run, 1 to 1000, most likely first">
              {({ id, describedBy }) => (
                <Input
                  id={id}
                  aria-describedby={describedBy}
                  type="number"
                  min={1}
                  max={1000}
                  value={c.limit == null ? "" : String(c.limit)}
                  onChange={(e) => set({ limit: num(e.target.value) })}
                />
              )}
            </Field>
            <Field label="Only these types" optional hint="e.g. PERSON, ORG; empty for all">
              {({ id, describedBy }) => (
                <Input
                  id={id}
                  aria-describedby={describedBy}
                  mono
                  defaultValue={typesText(c.types)}
                  onChange={(e) => set({ types: typesOf(e.target.value).length ? typesOf(e.target.value) : undefined })}
                />
              )}
            </Field>
          </>
        )}

        {node.type === "llm_judge" && (
          <>
            <Field label="Pairs a call" hint="How many pairs the model judges at once, 1 to 100">
              {({ id, describedBy }) => (
                <Input
                  id={id}
                  aria-describedby={describedBy}
                  type="number"
                  min={1}
                  max={100}
                  value={c.batch == null ? "" : String(c.batch)}
                  onChange={(e) => set({ batch: num(e.target.value) })}
                />
              )}
            </Field>
            <Field label="Instructions" optional hint="Anything the model should know, e.g. how your names are spelled">
              {({ id, describedBy }) => (
                <Textarea
                  id={id}
                  aria-describedby={describedBy}
                  rows={3}
                  value={String(c.instructions ?? "")}
                  onChange={(e) => set({ instructions: e.target.value })}
                />
              )}
            </Field>
            <Field label="Model" optional hint="Leave empty for the model in Settings → LLM">
              {({ id, describedBy }) => (
                <Input
                  id={id}
                  aria-describedby={describedBy}
                  mono
                  value={String(c.model ?? "")}
                  onChange={(e) => set({ model: e.target.value })}
                />
              )}
            </Field>
          </>
        )}

        {node.type === "apply_changes" && (
          <>
            <Checkbox
              checked={c.apply_above != null}
              onCheckedChange={(v) => set({ apply_above: v ? 0.95 : undefined, max_apply: v ? 25 : undefined })}
              label="Make the changes it’s sure of; propose the rest"
              disabled={readOnly}
            />
            {c.apply_above == null ? (
              <p className="text-[12.5px] text-fg-muted">Every change is proposed, for someone to accept.</p>
            ) : (
              <>
                <Field label="Sure enough at" hint="0 to 1: the model’s confidence (else the rules’) to make a change">
                  {({ id, describedBy }) => (
                    <Input
                      id={id}
                      aria-describedby={describedBy}
                      type="number"
                      min={0}
                      max={1}
                      step={0.05}
                      value={String(c.apply_above)}
                      onChange={(e) => set({ apply_above: num(e.target.value) ?? 0 })}
                    />
                  )}
                </Field>
                <Field label="At most" hint="Changes made a run, 0 to 1000; the rest are proposed">
                  {({ id, describedBy }) => (
                    <Input
                      id={id}
                      aria-describedby={describedBy}
                      type="number"
                      min={0}
                      max={1000}
                      value={c.max_apply == null ? "" : String(c.max_apply)}
                      onChange={(e) => set({ max_apply: num(e.target.value) })}
                    />
                  )}
                </Field>
              </>
            )}
          </>
        )}
      </fieldset>
    </div>
  );
}
