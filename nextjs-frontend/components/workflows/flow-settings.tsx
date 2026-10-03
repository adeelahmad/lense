"use client";

/**
 * Settings of the building blocks every workflow has: switch cases, set fields, templates, loops, the inputs and
 * returns of a body, and a custom node's parameters.
 */
import { Plus, Trash2 } from "lucide-react";

import { Button, IconButton } from "@/components/ui/button";
import { Checkbox, Field, Input, Select, Textarea } from "@/components/ui/field";
import {
  LOOP_ARGS,
  OPS,
  bodyPorts,
  type BodyKind,
  type CustomDef,
  type WfGraph,
  type WfNode,
} from "@/components/workflows/workflow-model";

/** A typed value: numbers and true/false as such, anything else as text. */
export function parseValue(s: string): unknown {
  const t = s.trim();
  if (t === "") return undefined;
  if (t === "true" || t === "false") return t === "true";
  if (!Number.isNaN(Number(t))) return Number(t);
  return s;
}

const showValue = (v: unknown) => (v == null ? "" : typeof v === "string" ? v : JSON.stringify(v));
const num = (s: string) => (s.trim() === "" ? undefined : Number(s));

type Case = { port: string; path?: string; op: string; value?: unknown };
type SetField = { key: string; path?: string; value?: unknown; template?: string };

function Test({
  value,
  onChange,
  pathHint,
}: {
  value: { path?: string; op?: string; value?: unknown };
  onChange: (v: { path?: string; op: string; value?: unknown }) => void;
  pathHint?: string;
}) {
  const op = value.op ?? "exists";
  const put = (patch: Partial<{ path?: string; op: string; value?: unknown }>) => {
    const next = { ...value, op, ...patch } as { path?: string; op: string; value?: unknown };
    if (!next.path) delete next.path;
    if (next.value === undefined || next.op === "exists" || next.op === "empty") delete next.value;
    onChange(next);
  };
  return (
    <div className="grid grid-cols-[1fr_1fr] gap-1.5">
      <Input
        aria-label="Path"
        mono
        placeholder={pathHint ?? "path (empty: all of it)"}
        value={value.path ?? ""}
        onChange={(e) => put({ path: e.target.value })}
      />
      <Select
        aria-label="Test"
        value={op}
        onChange={(e) => put({ op: e.target.value })}
        options={Object.entries(OPS).map(([v, label]) => ({ value: v, label }))}
      />
      {op !== "exists" && op !== "empty" && (
        <Input
          aria-label="Value"
          mono
          className="col-span-2"
          placeholder="value: text, a number, true or false"
          defaultValue={showValue(value.value)}
          onChange={(e) => put({ value: parseValue(e.target.value) })}
        />
      )}
    </div>
  );
}

export function FlowSettings({
  node,
  set,
  custom,
  bodyKind,
  onUpgrade,
}: {
  node: WfNode;
  set: (patch: Record<string, unknown>) => void;
  custom?: (id: number) => CustomDef | undefined;
  bodyKind: BodyKind;
  onUpgrade?: () => void;
}) {
  const c = node.config;
  switch (node.type) {
    case "switch": {
      const cases = (c.cases as Case[] | undefined) ?? [];
      const put = (k: number, v: Case | null) =>
        set({ cases: v ? cases.map((x, i) => (i === k ? v : x)) : cases.filter((_, i) => i !== k) });
      return (
        <>
          <Field label="Path" optional hint="What the cases test, unless a case names its own path">
            {({ id, describedBy }) => (
              <Input
                id={id}
                aria-describedby={describedBy}
                mono
                value={String(c.path ?? "")}
                placeholder="summary.kind"
                onChange={(e) => set({ path: e.target.value })}
              />
            )}
          </Field>
          <span className="label-caps">Cases, tested in turn</span>
          {cases.map((k, i) => (
            <div key={i} className="flex flex-col gap-1.5 rounded-sm border border-border p-2">
              <div className="flex items-center gap-1.5">
                <Input
                  aria-label="Case name"
                  mono
                  value={k.port}
                  placeholder="name, e.g. urgent"
                  onChange={(e) => put(i, { ...k, port: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_") })}
                />
                <IconButton label="Remove this case" size={30} onClick={() => put(i, null)}>
                  <Trash2 />
                </IconButton>
              </div>
              <Test
                value={k}
                onChange={(t) => put(i, { port: k.port, ...t })}
                pathHint={c.path ? String(c.path) : undefined}
              />
            </div>
          ))}
          <Button
            size="sm"
            variant="ghost"
            icon={<Plus />}
            onClick={() => set({ cases: [...cases, { port: `case${cases.length + 1}`, op: "exists" }] })}
          >
            Add a case
          </Button>
          <p className="text-[12px] text-fg-muted">What matches no case goes along default.</p>
        </>
      );
    }
    case "set": {
      const fields = (c.fields as SetField[] | undefined) ?? [];
      const ins = (c.inputs as string[] | undefined) ?? [];
      const put = (k: number, v: SetField | null) =>
        set({ fields: v ? fields.map((x, i) => (i === k ? v : x)) : fields.filter((_, i) => i !== k) });
      const src = (f: SetField) => ("template" in f ? "template" : "value" in f ? "value" : "path");
      return (
        <>
          <Field
            label="Inputs"
            optional
            hint="Names for several inputs, e.g. notes, entities; a field’s path then starts with one. Empty: one input."
          >
            {({ id, describedBy }) => (
              <Input
                id={id}
                aria-describedby={describedBy}
                mono
                defaultValue={ins.join(", ")}
                onChange={(e) => {
                  const names = e.target.value
                    .split(/[,\s]+/)
                    .map((x) => x.trim().toLowerCase())
                    .filter(Boolean);
                  set({ inputs: names.length ? names : undefined });
                }}
              />
            )}
          </Field>
          <Checkbox
            checked={Boolean(c.keep)}
            onCheckedChange={(v) => set({ keep: v || undefined })}
            label="Start from what came in (keep its keys)"
          />
          <span className="label-caps">Fields</span>
          {fields.map((f, i) => (
            <div key={i} className="flex flex-col gap-1.5 rounded-sm border border-border p-2">
              <div className="grid grid-cols-[1fr_auto_auto] items-center gap-1.5">
                <Input
                  aria-label="Key"
                  mono
                  value={f.key}
                  placeholder="key"
                  onChange={(e) => put(i, { ...f, key: e.target.value })}
                />
                <Select
                  aria-label="Comes from"
                  value={src(f)}
                  onChange={(e) =>
                    put(
                      i,
                      e.target.value === "template"
                        ? { key: f.key, template: "{{ input }}" }
                        : e.target.value === "value"
                          ? { key: f.key, value: "" }
                          : { key: f.key, path: "" },
                    )
                  }
                  options={[
                    { value: "path", label: "a path" },
                    { value: "value", label: "a value" },
                    { value: "template", label: "a template" },
                  ]}
                />
                <IconButton label="Remove this field" size={30} onClick={() => put(i, null)}>
                  <Trash2 />
                </IconButton>
              </div>
              {src(f) === "path" && (
                <Input
                  aria-label="Path"
                  mono
                  value={f.path ?? ""}
                  placeholder={ins.length > 1 ? `${ins[0]}.something` : "summary.tldr (empty: all of it)"}
                  onChange={(e) => put(i, { key: f.key, path: e.target.value })}
                />
              )}
              {src(f) === "value" && (
                <Input
                  aria-label="Value"
                  mono
                  defaultValue={showValue(f.value)}
                  onChange={(e) => put(i, { key: f.key, value: parseValue(e.target.value) ?? "" })}
                />
              )}
              {src(f) === "template" && (
                <Textarea
                  aria-label="Template"
                  rows={2}
                  className="font-mono text-[12.5px]"
                  value={f.template ?? ""}
                  onChange={(e) => put(i, { key: f.key, template: e.target.value })}
                />
              )}
            </div>
          ))}
          <Button
            size="sm"
            variant="ghost"
            icon={<Plus />}
            onClick={() => set({ fields: [...fields, { key: `field${fields.length + 1}`, path: "" }] })}
          >
            Add a field
          </Button>
        </>
      );
    }
    case "template":
      return (
        <>
          <Field
            label="Template"
            hint="Jinja: what came in is {{ input }}; {{ recording.title }}, {{ transcript }}… too"
          >
            {({ id, describedBy }) => (
              <Textarea
                id={id}
                aria-describedby={describedBy}
                rows={6}
                className="font-mono text-[12.5px]"
                value={String(c.template ?? "")}
                onChange={(e) => set({ template: e.target.value })}
              />
            )}
          </Field>
          <Checkbox
            checked={Boolean(c.json)}
            onCheckedChange={(v) => set({ json: v || undefined })}
            label="Read what it renders as JSON"
          />
        </>
      );
    case "for_each":
      return (
        <>
          <Field
            label="List"
            optional
            hint="A path to the list in what came in, e.g. action_items; empty if it is the list"
          >
            {({ id, describedBy }) => (
              <Input
                id={id}
                aria-describedby={describedBy}
                mono
                value={String(c.path ?? "")}
                placeholder="segments"
                onChange={(e) => set({ path: e.target.value })}
              />
            )}
          </Field>
          <Field label="At most" optional hint="Items to go over, 1 to 1000 (default 200)">
            {({ id, describedBy }) => (
              <Input
                id={id}
                aria-describedby={describedBy}
                type="number"
                min={1}
                max={1000}
                value={c.max_items == null ? "" : String(c.max_items)}
                onChange={(e) => set({ max_items: num(e.target.value) })}
              />
            )}
          </Field>
          <BodyNote node={node} />
        </>
      );
    case "repeat":
      return (
        <>
          <Field label="At most" hint="Rounds, 1 to 50">
            {({ id, describedBy }) => (
              <Input
                id={id}
                aria-describedby={describedBy}
                type="number"
                min={1}
                max={50}
                value={c.max_rounds == null ? "" : String(c.max_rounds)}
                onChange={(e) => set({ max_rounds: num(e.target.value) })}
              />
            )}
          </Field>
          <Checkbox
            checked={c.until != null}
            onCheckedChange={(v) => set({ until: v ? { op: "exists" } : undefined })}
            label="Stop when the result passes a test"
          />
          {c.until != null && (
            <Test value={c.until as { op: string }} onChange={(t) => set({ until: t })} pathHint="done" />
          )}
          <BodyNote node={node} />
        </>
      );
    case "group":
      return <BodyNote node={node} />;
    case "arg":
    case "return": {
      const fixed = LOOP_ARGS[bodyKind];
      return (
        <Field
          label="Name"
          hint={
            node.type === "return"
              ? fixed
                ? "A loop’s body returns out"
                : "The output it gives back on; Return nodes with the same name on different branches share it"
              : fixed
                ? `In this loop: ${fixed.join(", ")}`
                : "The input it stands for, e.g. text or entities"
          }
        >
          {({ id, describedBy }) =>
            fixed && node.type === "arg" ? (
              <Select
                id={id}
                aria-describedby={describedBy}
                value={String(c.name ?? "")}
                onChange={(e) => set({ name: e.target.value })}
                options={fixed.map((v) => ({ value: v, label: v }))}
              />
            ) : (
              <Input
                id={id}
                aria-describedby={describedBy}
                mono
                value={String(c.name ?? "")}
                disabled={Boolean(fixed)}
                onChange={(e) => set({ name: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_") })}
              />
            )
          }
        </Field>
      );
    }
    case "custom": {
      const d = custom?.(Number(c.node));
      if (!d)
        return <p className="text-[12.5px] text-red-dark">This custom node was removed or isn’t shared with you.</p>;
      const params = (c.params as Record<string, unknown> | undefined) ?? {};
      const put = (name: string, v: unknown) => {
        const next = { ...params };
        if (v === undefined) delete next[name];
        else next[name] = v;
        set({ params: Object.keys(next).length ? next : undefined });
      };
      return (
        <>
          {d.description && <p className="text-[12.5px] text-fg-secondary">{d.description}</p>}
          <p className="text-[12px] text-fg-muted">
            v{String(c.version ?? d.version)}
            {d.current > Number(c.version ?? d.current) ? ` · v${d.current} is saved` : " · the latest"} · by{" "}
            {d.owner_email ?? "someone"}
          </p>
          {d.current > Number(c.version ?? d.current) && onUpgrade && (
            <Button size="sm" variant="secondary" onClick={onUpgrade}>
              Use v{d.current}
            </Button>
          )}
          <a
            href={`/workflows/nodes/${d.id}`}
            target="_blank"
            rel="noreferrer"
            className="text-[12.5px] font-semibold text-fg-accent hover:underline"
          >
            {d.editable ? "Edit this custom node" : "See what’s inside"}
          </a>
          {(d.params ?? []).map((p) => (
            <Field key={p.name} label={p.label || p.name} optional hint={p.help ?? `Default: ${showValue(p.default)}`}>
              {({ id, describedBy }) =>
                p.kind === "bool" ? (
                  <Checkbox
                    checked={Boolean(params[p.name] ?? p.default)}
                    onCheckedChange={(v) => put(p.name, v)}
                    label={p.label || p.name}
                  />
                ) : p.kind === "choice" ? (
                  <Select
                    id={id}
                    aria-describedby={describedBy}
                    value={showValue(params[p.name] ?? p.default)}
                    onChange={(e) =>
                      put(
                        p.name,
                        (p.options ?? []).find((o) => showValue(o) === e.target.value),
                      )
                    }
                    options={(p.options ?? []).map((o) => ({ value: showValue(o), label: showValue(o) }))}
                  />
                ) : p.kind === "json" ? (
                  <Textarea
                    id={id}
                    aria-describedby={describedBy}
                    rows={3}
                    className="font-mono text-[12.5px]"
                    defaultValue={params[p.name] === undefined ? "" : JSON.stringify(params[p.name], null, 2)}
                    onChange={(e) => {
                      try {
                        put(p.name, e.target.value.trim() ? JSON.parse(e.target.value) : undefined);
                      } catch {
                        /* kept as typed until it is JSON */
                      }
                    }}
                  />
                ) : (
                  <Input
                    id={id}
                    aria-describedby={describedBy}
                    mono={p.kind === "number"}
                    type={p.kind === "number" ? "number" : "text"}
                    value={params[p.name] === undefined ? "" : String(params[p.name])}
                    placeholder={showValue(p.default)}
                    onChange={(e) =>
                      put(
                        p.name,
                        e.target.value === ""
                          ? undefined
                          : p.kind === "number"
                            ? Number(e.target.value)
                            : e.target.value,
                      )
                    }
                  />
                )
              }
            </Field>
          ))}
        </>
      );
    }
  }
  return null;
}

function BodyNote({ node }: { node: WfNode }) {
  const p = bodyPorts(node.config.body as WfGraph | undefined);
  const n = (node.config.body as WfGraph | undefined)?.nodes.length ?? 0;
  return (
    <p className="rounded-sm border border-border bg-surface px-3 py-2 text-[12.5px] text-fg-secondary">
      Its body has {n} nodes{p.inputs.length ? `, starting from ${p.inputs.join(", ")}` : ""}
      {p.outputs.length ? ` and returning ${p.outputs.join(", ")}` : ""}. Double-click the node to open it.
    </p>
  );
}
