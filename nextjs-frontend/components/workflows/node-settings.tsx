"use client";

import { useState } from "react";

import type { FieldDef, TemplateSummary } from "@/app/openapi-client/types.gen";
import { FlowSettings } from "@/components/workflows/flow-settings";
import {
  EPISODE_STYLES,
  OPS,
  SELECT_LIMITS,
  SINCE,
  VOCABULARIES,
  infoFor,
  paramRef,
  type BodyKind,
  type CustomDef,
  type Scope,
  type WfNode,
} from "@/components/workflows/workflow-model";
import { Checkbox, Field, Input, Select, Textarea } from "@/components/ui/field";

export type NsField = FieldDef & { namespace: string };
/** A feed a publishing node can add to, as the picker shows it. */
export type FeedOption = { id: number; title: string; namespace?: string | null; kind: string };
const STATUSES = ["new", "transcribed", "diarized", "analyzed", "error"];
const LENGTHS = [5, 10, 20];

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

/** Names typed with commas or spaces between them, as a list. */
const listOf = (s: string) =>
  s
    .split(/[,\s]+/)
    .map((x) => x.trim())
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
  custom,
  bodyKind = "workflow",
  onUpgrade,
  params,
  settings,
  peek,
  feeds,
  namespaces,
}: {
  node: WfNode;
  onChange: (n: WfNode) => void;
  templates: TemplateSummary[];
  fields: NsField[];
  readOnly?: boolean;
  problem?: string;
  scope?: Scope;
  custom?: (id: number) => CustomDef | undefined;
  bodyKind?: BodyKind;
  onUpgrade?: () => void;
  /** In a custom node: its parameters, which settings can be bound to. */
  params?: string[];
  /** The node type's settings (from the catalog), for binding them to parameters. */
  settings?: string[];
  /** What it passed on in the last try. */
  peek?: { status: string; value?: string | null; error?: string | null };
  /** The feeds a Publish to feed or Podcast episode node can add to (graph workflows). */
  feeds?: FeedOption[];
  /** The namespaces a publishing node can name; typed when not given. */
  namespaces?: string[];
}) {
  const info = infoFor(node.type, scope);
  // Settings bound to a parameter show as unset below; setting them again unbinds them.
  const c = Object.fromEntries(Object.entries(node.config).filter(([, v]) => !paramRef(v)));
  const bound = Object.entries(node.config).filter(([, v]) => paramRef(v));
  const set = (patch: Record<string, unknown>) => {
    const config = { ...node.config, ...patch };
    for (const k of Object.keys(config)) if (config[k] === undefined || config[k] === "") delete config[k];
    onChange({ ...node, config });
  };
  return (
    <div className="flex flex-col gap-3" key={node.id}>
      <div>
        <h2 className="text-[15px] font-bold text-fg">
          {node.label ||
            (node.type === "custom" ? custom?.(Number(node.config.node))?.name : undefined) ||
            info?.label ||
            node.type}
        </h2>
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
      {peek && (
        <div className="flex flex-col gap-1 rounded-sm border border-border bg-surface px-3 py-2">
          <span className="label-caps">
            Last try: {peek.status === "done" ? "ran" : peek.status === "failed" ? "failed" : "skipped"}
          </span>
          {peek.error && <p className="text-[12.5px] text-red-dark">{peek.error}</p>}
          {peek.value && (
            <pre className="max-h-[220px] overflow-auto whitespace-pre-wrap break-all font-mono text-[11.5px] text-fg">
              {pretty(peek.value)}
            </pre>
          )}
        </div>
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

        <FlowSettings node={node} set={set} custom={custom} bodyKind={bodyKind} onUpgrade={onUpgrade} />

        {node.type === "llm" && (
          <>
            <Field label="Prompt template" hint="What came in is {{ input }}; the asset is there as in any template">
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

        {node.type === "ask_model" && (
          <>
            <Field label="Prompt" hint="What came in is {{ input }}, e.g. Summarise {{ input.text }} in one line">
              {({ id, describedBy }) => (
                <Textarea
                  id={id}
                  aria-describedby={describedBy}
                  mono
                  rows={5}
                  value={String(c.prompt ?? "")}
                  onChange={(e) => set({ prompt: e.target.value })}
                />
              )}
            </Field>
            <Field label="System prompt" optional>
              {({ id }) => (
                <Textarea
                  id={id}
                  rows={2}
                  value={String(c.system ?? "")}
                  onChange={(e) => set({ system: e.target.value })}
                />
              )}
            </Field>
            <Checkbox
              checked={Boolean(c.json)}
              onCheckedChange={(v) => set({ json: v || undefined })}
              label="The reply is JSON: pass it on as data"
            />
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

        {node.type === "call_tool" && (
          <>
            <Field
              label="Tool"
              hint="Its name: one of the assistant’s own (search_transcripts, find_entities…) or an extension’s"
            >
              {({ id, describedBy }) => (
                <Input
                  id={id}
                  aria-describedby={describedBy}
                  mono
                  value={String(c.tool ?? "")}
                  placeholder="search_transcripts"
                  onChange={(e) => set({ tool: e.target.value })}
                />
              )}
            </Field>
            <ArgsField value={c.args} onChange={(args) => set({ args })} />
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
              hint="One per line, as Name|TYPE: always found, wherever they appear"
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

        {node.type === "entity_filter" && (
          <>
            <Field label="Keep only these types" optional hint="e.g. PERSON, ORG; empty keeps every type">
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
            <Field label="Leave out these types" optional hint="e.g. DATE, NUMBER">
              {({ id, describedBy }) => (
                <Input
                  id={id}
                  aria-describedby={describedBy}
                  mono
                  defaultValue={typesText(c.skip_types)}
                  onChange={(e) =>
                    set({ skip_types: typesOf(e.target.value).length ? typesOf(e.target.value) : undefined })
                  }
                />
              )}
            </Field>
            <Lines
              label="Leave out these names"
              hint="One per line; case and spacing don’t matter"
              placeholder={"Thanks\nOkay"}
              value={(c.skip_names as string[] | undefined) ?? []}
              show={(v) => String(v)}
              parse={(lines) => set({ skip_names: lines.length ? lines : undefined })}
            />
            <Field label="Name matches" optional hint="A regular expression, case aside, e.g. ^[A-Z] or corp|inc">
              {({ id, describedBy }) => (
                <Input
                  id={id}
                  aria-describedby={describedBy}
                  mono
                  value={String(c.pattern ?? "")}
                  onChange={(e) => set({ pattern: e.target.value || undefined })}
                />
              )}
            </Field>
            <Field label="At least this long" optional hint="Characters in the name">
              {({ id, describedBy }) => (
                <Input
                  id={id}
                  aria-describedby={describedBy}
                  type="number"
                  min={1}
                  value={c.min_length == null ? "" : String(c.min_length)}
                  onChange={(e) => set({ min_length: num(e.target.value) })}
                />
              )}
            </Field>
            <Field label="Said at least" optional hint="Times: its mentions, else how often the list names it">
              {({ id, describedBy }) => (
                <Input
                  id={id}
                  aria-describedby={describedBy}
                  type="number"
                  min={1}
                  value={c.min_mentions == null ? "" : String(c.min_mentions)}
                  onChange={(e) => set({ min_mentions: num(e.target.value) })}
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

        {node.type === "classify" && (
          <>
            <Field label="Choose from" hint="The decision model picks one, else the model in Settings → LLM">
              {({ id, describedBy }) => (
                <Select
                  id={id}
                  aria-describedby={describedBy}
                  value={String(c.vocabulary ?? "")}
                  onChange={(e) =>
                    set(
                      e.target.value
                        ? { vocabulary: e.target.value, labels: undefined }
                        : { vocabulary: undefined, labels: (c.labels as string[] | undefined) ?? [] },
                    )
                  }
                  options={[
                    { value: "", label: "Labels you give" },
                    ...Object.entries(VOCABULARIES).map(([value, label]) => ({ value, label })),
                  ]}
                />
              )}
            </Field>
            {c.vocabulary == null && (
              <Lines
                key={`${node.id}-labels`}
                label="Labels"
                hint="One per line; what a label means after a colon helps it choose"
                placeholder={"security: exploits and attacks\nhealth: medicine and research"}
                value={(c.labels as string[] | undefined) ?? []}
                show={(v) => String(v)}
                parse={(lines) => set({ labels: lines })}
              />
            )}
            <Field label="Question" optional hint="Ask it your way, e.g. Which desk should this go to?">
              {({ id, describedBy }) => (
                <Input
                  id={id}
                  aria-describedby={describedBy}
                  value={String(c.question ?? "")}
                  onChange={(e) => set({ question: e.target.value || undefined })}
                />
              )}
            </Field>
            <Field label="Classify" hint="What came in is read as text">
              {({ id, describedBy }) => (
                <Select
                  id={id}
                  aria-describedby={describedBy}
                  value={c.of === "input" ? "input" : "resource"}
                  onChange={(e) =>
                    set(e.target.value === "input" ? { of: "input", apply: undefined } : { of: undefined })
                  }
                  options={[
                    { value: "resource", label: "The asset" },
                    { value: "input", label: "What comes in" },
                  ]}
                />
              )}
            </Field>
            <Checkbox
              checked={Boolean(c.apply)}
              onCheckedChange={(v) => set({ apply: v || undefined })}
              label="Act on a sure answer: set the content type, suggest the topic or add the label as a tag"
              disabled={readOnly || c.of === "input"}
            />
          </>
        )}

        {node.type === "output" && (
          <Field label="Output name" hint="Saved on the asset as outputs.<name>; lowercase letters, digits and _">
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
                ? "Set on assets in the field’s namespace; elsewhere the node fails and says why"
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
        {(node.type === "select_assets" || node.type === "select_activities") && (
          <>
            {node.type === "select_activities" && (
              <Field
                label="Kinds"
                optional
                hint="e.g. asset.added, import.received; with what’s under each. Empty for every kind"
              >
                {({ id, describedBy }) => (
                  <Input
                    id={id}
                    aria-describedby={describedBy}
                    mono
                    defaultValue={((c.kinds as string[] | undefined) ?? []).join(", ")}
                    onChange={(e) => set({ kinds: listOf(e.target.value).length ? listOf(e.target.value) : undefined })}
                  />
                )}
              </Field>
            )}
            <Field label="Since" hint="What the routine’s last run didn’t see, or a period back from now">
              {({ id, describedBy }) => (
                <Select
                  id={id}
                  aria-describedby={describedBy}
                  value={String(c.since ?? "last_run")}
                  onChange={(e) => set({ since: e.target.value })}
                  options={Object.entries(SINCE).map(([value, label]) => ({
                    value,
                    label: value === "last_run" ? "The routine’s last run" : label[0].toUpperCase() + label.slice(1),
                  }))}
                />
              )}
            </Field>
            <Field
              label="At most"
              hint={`${node.type === "select_assets" ? "Resources" : "Activities"} a run, 1 to ${SELECT_LIMITS[node.type]}, newest first`}
            >
              {({ id, describedBy }) => (
                <Input
                  id={id}
                  aria-describedby={describedBy}
                  type="number"
                  min={1}
                  max={SELECT_LIMITS[node.type]}
                  value={c.limit == null ? "" : String(c.limit)}
                  onChange={(e) => set({ limit: num(e.target.value) })}
                />
              )}
            </Field>
            <Field
              label="Words"
              optional
              hint={node.type === "select_assets" ? "In the title" : "In the activity’s summary"}
            >
              {({ id, describedBy }) => (
                <Input
                  id={id}
                  aria-describedby={describedBy}
                  value={String(c.q ?? "")}
                  maxLength={200}
                  onChange={(e) => set({ q: e.target.value })}
                />
              )}
            </Field>
            {node.type === "select_assets" && (
              <Field label="Status" optional hint="Only resources at this stage; empty for any">
                {({ id, describedBy }) => (
                  <Select
                    id={id}
                    aria-describedby={describedBy}
                    value={String((c.status as string[] | undefined)?.[0] ?? "")}
                    onChange={(e) => set({ status: e.target.value ? [e.target.value] : undefined })}
                    options={[{ value: "", label: "Any" }, ...STATUSES.map((v) => ({ value: v, label: v }))]}
                  />
                )}
              </Field>
            )}
            <Field label="Namespaces" optional hint="Empty for the routine’s namespaces; naming some narrows to them">
              {({ id, describedBy }) => (
                <Input
                  id={id}
                  aria-describedby={describedBy}
                  mono
                  defaultValue={((c.namespaces as string[] | undefined) ?? []).join(", ")}
                  onChange={(e) =>
                    set({ namespaces: listOf(e.target.value).length ? listOf(e.target.value) : undefined })
                  }
                />
              )}
            </Field>
            {node.type === "select_assets" && (
              <Checkbox
                checked={Boolean(c.include_episodes)}
                onCheckedChange={(v) => set({ include_episodes: v === true ? true : undefined })}
                label="Include podcast episodes"
                disabled={readOnly}
              />
            )}
          </>
        )}

        {(node.type === "publish_feed" || node.type === "podcast_episode") && (
          <>
            <Field
              label="Feed"
              optional={node.type === "podcast_episode"}
              hint={
                feeds && !feeds.length
                  ? "No feeds yet: make one with Feeds on the Podcasts page"
                  : node.type === "podcast_episode"
                    ? "The episode is added to it once it’s made; none just makes the episode"
                    : "In a namespace the routine covers"
              }
            >
              {({ id, describedBy }) => (
                <Select
                  id={id}
                  aria-describedby={describedBy}
                  value={c.feed == null ? "" : String(c.feed)}
                  onChange={(e) => set({ feed: e.target.value ? Number(e.target.value) : undefined })}
                  options={[
                    { value: "", label: node.type === "podcast_episode" ? "None" : "Choose a feed" },
                    ...(feeds ?? []).map((f) => ({
                      value: String(f.id),
                      label: `${f.title}${f.namespace ? ` · ${f.namespace}` : ""} · ${f.kind === "podcast" ? "podcast" : "RSS"}`,
                    })),
                    ...(c.feed != null && !(feeds ?? []).some((f) => f.id === Number(c.feed))
                      ? [{ value: String(c.feed), label: `Feed #${String(c.feed)}` }]
                      : []),
                  ]}
                />
              )}
            </Field>
            {node.type === "publish_feed" && (
              <Field label="Title" optional hint="For text that comes in, e.g. from a Template node">
                {({ id, describedBy }) => (
                  <Input
                    id={id}
                    aria-describedby={describedBy}
                    value={String(c.title ?? "")}
                    maxLength={200}
                    onChange={(e) => set({ title: e.target.value })}
                  />
                )}
              </Field>
            )}
          </>
        )}

        {node.type === "podcast_episode" && (
          <>
            <Field
              label="Namespace"
              optional
              hint="Where the episode goes: empty for the feed’s, else the podcasts namespace"
            >
              {({ id, describedBy }) =>
                namespaces ? (
                  <Select
                    id={id}
                    aria-describedby={describedBy}
                    value={String(c.namespace ?? "")}
                    onChange={(e) => set({ namespace: e.target.value || undefined })}
                    options={[{ value: "", label: "The feed’s" }, ...namespaces.map((n) => ({ value: n, label: n }))]}
                  />
                ) : (
                  <Input
                    id={id}
                    aria-describedby={describedBy}
                    mono
                    value={String(c.namespace ?? "")}
                    onChange={(e) => set({ namespace: e.target.value })}
                  />
                )
              }
            </Field>
            <Field label="Angle" optional hint="What to focus on, or who it’s for">
              {({ id, describedBy }) => (
                <Textarea
                  id={id}
                  aria-describedby={describedBy}
                  rows={2}
                  value={String(c.prompt ?? "")}
                  maxLength={2000}
                  onChange={(e) => set({ prompt: e.target.value })}
                />
              )}
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Length">
                {({ id }) => (
                  <Select
                    id={id}
                    value={String(c.length ?? 10)}
                    onChange={(e) => set({ length: Number(e.target.value) })}
                    options={[...new Set([...LENGTHS, Number(c.length ?? 10)])]
                      .sort((a, b) => a - b)
                      .map((n) => ({ value: String(n), label: `About ${n} minutes` }))}
                  />
                )}
              </Field>
              <Field label="Style">
                {({ id }) => (
                  <Select
                    id={id}
                    value={String(c.style ?? "deep-dive")}
                    onChange={(e) => set({ style: e.target.value })}
                    options={Object.entries(EPISODE_STYLES).map(([value, label]) => ({ value, label }))}
                  />
                )}
              </Field>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Most sources" hint="Resources an episode draws on, 1 to 20">
                {({ id, describedBy }) => (
                  <Input
                    id={id}
                    aria-describedby={describedBy}
                    type="number"
                    min={1}
                    max={20}
                    placeholder="10"
                    value={c.max_sources == null ? "" : String(c.max_sources)}
                    onChange={(e) => set({ max_sources: num(e.target.value) })}
                  />
                )}
              </Field>
              <Field label="Fewest" hint="No episode under this many">
                {({ id, describedBy }) => (
                  <Input
                    id={id}
                    aria-describedby={describedBy}
                    type="number"
                    min={1}
                    max={20}
                    placeholder="1"
                    value={c.min_sources == null ? "" : String(c.min_sources)}
                    onChange={(e) => set({ min_sources: num(e.target.value) })}
                  />
                )}
              </Field>
            </div>
            <Field label="Title" optional hint="Left empty, the script’s own title is used">
              {({ id, describedBy }) => (
                <Input
                  id={id}
                  aria-describedby={describedBy}
                  value={String(c.title ?? "")}
                  maxLength={200}
                  onChange={(e) => set({ title: e.target.value })}
                />
              )}
            </Field>
          </>
        )}
        {params && (bound.length > 0 || (params.length > 0 && (settings?.length ?? 0) > 0)) && (
          <div className="flex flex-col gap-1.5 border-t border-border pt-3">
            <span className="label-caps">Set by a parameter</span>
            <p className="text-[12px] text-fg-muted">
              Where the custom node is used, people fill these in. Changing a setting above unbinds it.
            </p>
            {(settings ?? [])
              .filter((k) => k !== "body")
              .map((k) => (
                <div key={k} className="grid grid-cols-[110px_1fr] items-center gap-2">
                  <code className="truncate font-mono text-[12px] text-fg">{k}</code>
                  <Select
                    aria-label={`What sets ${k}`}
                    size="sm"
                    value={paramRef(node.config[k]) ?? ""}
                    onChange={(e) => set({ [k]: e.target.value ? { $param: e.target.value } : undefined })}
                    options={[
                      { value: "", label: "Its setting above" },
                      ...params.map((p) => ({ value: p, label: `Parameter ${p}` })),
                    ]}
                  />
                </div>
              ))}
          </div>
        )}
      </fieldset>
    </div>
  );
}

function pretty(v: string): string {
  try {
    return JSON.stringify(JSON.parse(v), null, 2);
  } catch {
    return v;
  }
}

/** A call tool node's fixed arguments, as JSON; what comes in is laid over them. */
function ArgsField({
  value,
  onChange,
}: {
  value: unknown;
  onChange: (v: Record<string, unknown> | undefined) => void;
}) {
  const [text, setText] = useState(() => (value ? JSON.stringify(value, null, 2) : ""));
  const [bad, setBad] = useState(false);
  return (
    <Field
      label="Arguments"
      optional
      hint={'JSON, e.g. {"limit": 5}; what comes in (an object) is laid over them'}
      error={bad ? "That isn’t a JSON object" : undefined}
    >
      {({ id, describedBy }) => (
        <Textarea
          id={id}
          aria-describedby={describedBy}
          mono
          rows={3}
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            if (!e.target.value.trim()) {
              setBad(false);
              onChange(undefined);
              return;
            }
            try {
              const v = JSON.parse(e.target.value);
              const ok = v != null && typeof v === "object" && !Array.isArray(v);
              setBad(!ok);
              if (ok) onChange(v as Record<string, unknown>);
            } catch {
              setBad(true);
            }
          }}
        />
      )}
    </Field>
  );
}
