"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Blocks, Check, Play, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { Extensions } from "@/app/openapi-client";
import { bodyLabel, KIND_LABEL, ORIGIN_LABEL } from "@/components/extensions/extensions-page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox, Field, Input, Select, Switch, Textarea } from "@/components/ui/field";
import { Panel } from "@/components/ui/panel";
import { EmptyState, PageHeader, Skeleton } from "@/components/ui/states";
import { Segmented } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/toast";
import { VisibilityFields } from "@/components/workflows/save-custom-dialog";
import { data, useApiClient } from "@/lib/api/browser";
import { relative } from "@/lib/format";

/** Manifests to start from, one per kind. */
export const STARTERS: Record<string, string> = {
  skill: `---
name: meeting_recap
kind: skill
description: Recaps a meeting
when: someone asks for a recap of a meeting
---

Find the meeting, read it, and answer with three bullets: decisions, owners, dates.
`,
  tool: `---
name: translate
kind: tool
description: Translate text into another language.
effect: read
params:
  - {name: text, kind: text, required: true}
  - {name: language, kind: text, options: [French, German, Swedish], default: French}
---

Translate into {{language}}: {{text}}
`,
  hook: `name: house_style
kind: hook
event: message
action:
  type: context
  text: Answer in British English, in short paragraphs.
`,
  plugin: `name: tickets
kind: plugin
description: File tickets and keep answers short
items:
  - kind: tool
    name: file_ticket
    description: File a ticket in the tracker.
    effect: change
    params: [{name: title, kind: text, required: true}]
    run: {type: http, method: POST, url: "https://tracker.example.com/api/tickets", body: {title: "{{title}}"}}
  - kind: hook
    name: short_answers
    event: message
    action: {type: context, text: Keep answers under five sentences.}
`,
};

type Item = { kind: string; name: string; spec: { effect?: string } };

/** One extension: its manifest (written as code, checked as you go), on or off, who can use it, trying its tools,
 * and its versions. With no id, a new one. */
export function ExtensionEditor({ id }: { id?: number }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const router = useRouter();
  const ext = useQuery({
    queryKey: ["extensions", id],
    queryFn: () => data(Extensions.getExtension({ client, path: { eid: id as number } })),
    enabled: id != null,
  });
  const e = ext.data;
  const [kind, setKind] = useState("skill");
  const [text, setText] = useState(STARTERS.skill);
  const [notes, setNotes] = useState("");
  const [checked, setChecked] = useState<{ ok: boolean; message: string } | null>(null);
  useEffect(() => {
    if (e) setText(e.manifest);
  }, [e]);

  const check = useMutation({
    mutationFn: () => data(Extensions.checkManifest({ client, body: { text } })),
    onSuccess: (r) => {
      const m = r.manifest as { kind?: string; name?: string };
      setChecked({
        ok: true,
        message: `A ${KIND_LABEL[m.kind ?? ""]?.toLowerCase() ?? "manifest"} called ${m.name}: looks right.`,
      });
    },
    onError: (err) => setChecked({ ok: false, message: (err as Error).message }),
  });
  const save = useMutation({
    mutationFn: async () => {
      if (id == null) return (await data(Extensions.createExtension({ client, body: { text } }))).id;
      await data(
        Extensions.createExtensionVersion({ client, path: { eid: id }, body: { text, notes: notes || null } }),
      );
      return id;
    },
    onSuccess: (eid) => {
      void qc.invalidateQueries({ queryKey: ["extensions"] });
      setNotes("");
      setChecked(null);
      toast({ tone: "green", title: id == null ? "Added to the assistant" : "Saved a new version" });
      if (id == null) router.push(`/extensions/${eid}`);
    },
    onError: (err) => setChecked({ ok: false, message: (err as Error).message }),
  });

  if (id != null && ext.isLoading) return <Skeleton className="m-6 h-96" />;
  if (id != null && (ext.error || !e))
    return (
      <EmptyState tone="error" icon={<Blocks />} title="Couldn’t load this extension">
        {(ext.error as Error | null)?.message ?? "It may have been removed."}
      </EmptyState>
    );
  const dirty = e ? text !== e.manifest : true;
  const editable = id == null || Boolean(e?.editable);

  return (
    <div className="flex flex-col gap-4 px-4 pb-10 pt-[18px] md:px-6">
      <div className="text-[13px]">
        <Link href="/extensions" className="text-fg-accent hover:underline">
          Extensions
        </Link>
      </div>
      <PageHeader
        title={e ? <span className="font-mono">{e.name}</span> : "New extension"}
        meta={
          e ? (
            <span className="flex flex-wrap items-center gap-2">
              <Badge tone="intent">{KIND_LABEL[e.kind]}</Badge>
              {bodyLabel(e)}
              <span>
                v{e.version}
                {e.origin ? `, made in ${ORIGIN_LABEL[e.origin] ?? e.origin}` : ""}
              </span>
            </span>
          ) : (
            "Write it as a manifest: Markdown with YAML frontmatter, or YAML"
          )
        }
      />
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
        <Panel
          title="Manifest"
          subtitle={
            id == null
              ? "Start from an example, change it, check it, and save."
              : editable
                ? "Change it and save a new version: conversations use the new one at once."
                : "Only its owner or an admin changes it."
          }
          actions={
            id == null ? (
              <Segmented
                label="Start from"
                value={kind}
                onChange={(k: string) => {
                  setKind(k);
                  setText(STARTERS[k]);
                  setChecked(null);
                }}
                items={Object.keys(STARTERS).map((k) => ({ value: k, label: KIND_LABEL[k] }))}
              />
            ) : undefined
          }
        >
          <div className="flex flex-col gap-3">
            <Textarea
              aria-label="Manifest"
              mono
              rows={22}
              spellCheck={false}
              readOnly={!editable}
              value={text}
              onChange={(ev) => {
                setText(ev.target.value);
                setChecked(null);
              }}
            />
            {checked && (
              <p
                role={checked.ok ? "status" : "alert"}
                className={checked.ok ? "text-[13px] text-green-dark" : "text-[13px] text-red-dark"}
              >
                {checked.message}
              </p>
            )}
            {editable && (
              <div className="flex flex-wrap items-end gap-2">
                {id != null && (
                  <Field label="What changed" optional className="min-w-[220px] flex-1">
                    {({ id: fid }) => <Input id={fid} value={notes} onChange={(ev) => setNotes(ev.target.value)} />}
                  </Field>
                )}
                <Button variant="secondary" icon={<Check />} disabled={check.isPending} onClick={() => check.mutate()}>
                  Check
                </Button>
                <Button variant="primary" disabled={!dirty || save.isPending} onClick={() => save.mutate()}>
                  {save.isPending ? "Saving…" : id == null ? "Add to the assistant" : "Save version"}
                </Button>
              </div>
            )}
          </div>
        </Panel>
        {e && <SidePanels id={e.id} />}
      </div>
    </div>
  );
}

function SidePanels({ id }: { id: number }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const router = useRouter();
  const { data: e } = useQuery({
    queryKey: ["extensions", id],
    queryFn: () => data(Extensions.getExtension({ client, path: { eid: id } })),
  });
  const [visibility, setVisibility] = useState("private");
  const [namespaces, setNamespaces] = useState<string[]>([]);
  useEffect(() => {
    if (!e) return;
    setVisibility(e.visibility ?? "private");
    setNamespaces(e.namespaces ?? []);
  }, [e]);
  const patch = useMutation({
    mutationFn: (body: {
      enabled?: boolean;
      visibility?: "private" | "namespace" | "everyone";
      namespaces?: string[];
    }) => data(Extensions.updateExtension({ client, path: { eid: id }, body })),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["extensions"] }),
    onError: (err) => toast({ tone: "red", title: "Couldn’t change it", body: (err as Error).message }),
  });
  const remove = useMutation({
    mutationFn: () => data(Extensions.deleteExtension({ client, path: { eid: id } })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["extensions"] });
      toast({ title: "Removed from the assistant" });
      router.push("/extensions");
    },
  });
  if (!e) return null;
  const tools: Item[] =
    e.kind === "tool"
      ? [{ kind: "tool", name: e.name, spec: e.spec as Item["spec"] }]
      : e.kind === "plugin"
        ? ((e.spec.items as Item[]) ?? []).filter((it) => it.kind === "tool")
        : [];
  return (
    <div className="flex flex-col gap-4">
      <Panel title="In conversations">
        <div className="flex flex-col gap-3">
          <Switch
            label={e.enabled ? "On" : "Off"}
            checked={e.enabled}
            disabled={!e.editable || patch.isPending}
            onCheckedChange={(v) => patch.mutate({ enabled: v })}
          />
          {e.editable ? (
            <>
              <VisibilityFields
                visibility={visibility}
                namespaces={namespaces}
                onChange={(v, ns) => {
                  setVisibility(v);
                  setNamespaces(ns);
                }}
              />
              <Button
                size="sm"
                disabled={visibility === e.visibility && namespaces.join() === (e.namespaces ?? []).join()}
                onClick={() =>
                  patch.mutate({ visibility: visibility as "private" | "namespace" | "everyone", namespaces })
                }
              >
                Save who can use it
              </Button>
            </>
          ) : (
            <p className="text-[13px] text-fg-secondary">Shared with you by {e.owner_email}.</p>
          )}
        </div>
      </Panel>
      {tools.length > 0 && <TryPanel id={id} tools={tools} />}
      <Panel title="Versions">
        <ul className="flex flex-col gap-1.5 text-[13px]">
          {(e.history ?? []).map((h) => (
            <li key={h.version} className="flex items-baseline gap-2">
              <code className="font-mono text-[12px] font-medium">v{h.version}</code>
              <span className="min-w-0 flex-1 truncate text-fg-secondary">
                {h.notes || (h.origin ? `made in ${ORIGIN_LABEL[h.origin] ?? h.origin}` : "")}
              </span>
              <span className="tabular shrink-0 text-fg-muted">{relative(h.created_at)}</span>
            </li>
          ))}
        </ul>
      </Panel>
      {e.editable && (
        <Button
          variant="danger-ghost"
          icon={<Trash2 />}
          disabled={remove.isPending}
          onClick={() => {
            if (window.confirm(`Remove ${e.name} from the assistant?`)) remove.mutate();
          }}
        >
          Remove
        </Button>
      )}
    </div>
  );
}

function TryPanel({ id, tools }: { id: number; tools: Item[] }) {
  const client = useApiClient();
  const [tool, setTool] = useState(tools[0].name);
  const [args, setArgs] = useState("{}");
  const [confirm, setConfirm] = useState(false);
  const current = useMemo(() => tools.find((t) => t.name === tool) ?? tools[0], [tools, tool]);
  const changes = current.spec.effect === "change";
  const run = useMutation({
    mutationFn: async () => {
      let parsed: Record<string, unknown>;
      try {
        parsed = JSON.parse(args || "{}");
      } catch {
        throw new Error('The arguments aren’t JSON, e.g. {"text": "hello"}');
      }
      return data(Extensions.testExtension({ client, path: { eid: id }, body: { tool, args: parsed, confirm } }));
    },
  });
  return (
    <Panel title="Try it" subtitle={changes ? "This tool changes something: trying it really runs it." : undefined}>
      <div className="flex flex-col gap-3">
        {tools.length > 1 && (
          <Field label="Tool">
            {({ id: fid }) => (
              <Select
                id={fid}
                value={tool}
                onChange={(ev) => setTool(ev.target.value)}
                options={tools.map((t) => t.name)}
              />
            )}
          </Field>
        )}
        <Field label="Arguments (JSON)">
          {({ id: fid }) => (
            <Textarea
              id={fid}
              mono
              rows={3}
              spellCheck={false}
              value={args}
              onChange={(ev) => setArgs(ev.target.value)}
            />
          )}
        </Field>
        {changes && <Checkbox checked={confirm} onCheckedChange={setConfirm} label="Yes, really run it" />}
        <Button
          size="sm"
          icon={<Play />}
          disabled={run.isPending || (changes && !confirm)}
          onClick={() => run.mutate()}
        >
          {run.isPending ? "Running…" : "Run"}
        </Button>
        {run.error && (
          <p role="alert" className="text-[13px] text-red-dark">
            {(run.error as Error).message}
          </p>
        )}
        {run.data && (
          <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-md bg-surface-neutral p-3 font-mono text-[12px] text-fg">
            {JSON.stringify(run.data.output, null, 2)}
          </pre>
        )}
      </div>
    </Panel>
  );
}
