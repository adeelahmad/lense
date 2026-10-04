"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Wrench } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { Extensions } from "@/app/openapi-client";
import type { TemplateSummary } from "@/app/openapi-client/types.gen";
import { useWorkflowCatalog } from "@/components/pipelines/catalog-header";
import { Button } from "@/components/ui/button";
import { Checkbox, Field, Input, Select, Textarea } from "@/components/ui/field";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { GraphEditor } from "@/components/workflows/graph-editor";
import { VisibilityFields } from "@/components/workflows/save-custom-dialog";
import {
  bodyPorts,
  cleanGraph,
  normalize,
  problems as findProblems,
  sameGraph,
  starterTool,
  type CustomDef,
  type WfGraph,
} from "@/components/workflows/workflow-model";
import { data, useApiClient } from "@/lib/api/browser";

type Param = { name: string; kind?: string; required?: boolean; description?: string | null };

const KINDS: Record<string, string> = {
  text: "Text",
  number: "Number",
  integer: "Whole number",
  bool: "Yes or no",
  json: "JSON object",
  list: "List",
};
const NAME_RX = /^[a-z][a-z0-9_]{1,40}$/;

type Spec = { params?: Param[]; effect?: string; run?: { type?: string; graph?: WfGraph } };

/** An assistant tool drawn on the canvas: its Input nodes are its parameters, its Return nodes what it gives back.
 * Saved as an extension (a tool whose body is the graph), versioned like any other. */
export function ToolGraphEditor({ id }: { id?: number }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const router = useRouter();
  const creating = id == null;
  const q = useQuery({
    queryKey: ["extensions", id],
    queryFn: () => data(Extensions.getExtension({ client, path: { eid: id as number } })),
    enabled: !creating,
  });
  const catalog = useWorkflowCatalog();
  const customDefs = ((catalog.data?.custom_nodes ?? []) as unknown as CustomDef[]).filter((d) =>
    d.scopes.includes("tool"),
  );
  const base = q.data;
  const spec = (base?.spec ?? {}) as Spec;

  const [graph, setGraph] = useState<WfGraph>(starterTool);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [effect, setEffect] = useState("read");
  const [params, setParams] = useState<Record<string, Param>>({});
  const [visibility, setVisibility] = useState("private");
  const [namespaces, setNamespaces] = useState<string[]>([]);
  const [notes, setNotes] = useState("");
  useEffect(() => {
    if (!base) return;
    setGraph(normalize(spec.run?.graph ?? starterTool()));
    setName(base.name);
    setDescription(base.description ?? "");
    setEffect(spec.effect ?? "read");
    setParams(Object.fromEntries((spec.params ?? []).map((p) => [p.name, p])));
  }, [base]);

  // its parameters are its Input nodes, top to bottom; each keeps the kind and description set for it
  const argNames = useMemo(() => bodyPorts(graph).inputs, [graph]);
  const paramList: Param[] = argNames.map((n) => ({ kind: "text", ...params[n], name: n }));
  const custom = (cid: number) => customDefs.find((d) => d.id === cid);
  const problems = findProblems(graph, () => undefined, "tool", "custom", custom);
  const firstProblem =
    (!creating || NAME_RX.test(name) ? undefined : "Name it with lowercase letters, digits and _, e.g. summarise") ??
    (description.trim() ? undefined : "Say what it does: the assistant reads it to decide when to call it") ??
    (bodyPorts(graph).outputs.length ? undefined : "Add a Return node: what the tool gives back") ??
    Object.values(problems)[0];
  const editable = creating || Boolean(base?.editable);
  const dirty =
    creating ||
    (base != null &&
      (!sameGraph(normalize(spec.run?.graph ?? starterTool()), graph) ||
        description !== (base.description ?? "") ||
        effect !== (spec.effect ?? "read") ||
        JSON.stringify(paramList) !==
          JSON.stringify(
            argNames.map((n) => ({ kind: "text", ...(spec.params ?? []).find((p) => p.name === n), name: n })),
          )));

  const save = useMutation({
    mutationFn: async () => {
      const manifest = {
        name: creating ? name : base!.name,
        kind: "tool",
        description: description.trim(),
        effect,
        params: paramList.map((p) => ({
          name: p.name,
          kind: p.kind ?? "text",
          ...(p.required ? { required: true } : {}),
          ...(p.description ? { description: p.description } : {}),
        })),
        run: { type: "graph", graph: cleanGraph(graph) },
        ...(creating ? { visibility, namespaces } : {}),
      };
      if (creating)
        return (await data(Extensions.createExtension({ client, body: { manifest, origin: "canvas" } }))).id;
      await data(
        Extensions.createExtensionVersion({
          client,
          path: { eid: id },
          body: { manifest, origin: "canvas", notes: notes.trim() || null },
        }),
      );
      return id;
    },
    onSuccess: (eid) => {
      void qc.invalidateQueries({ queryKey: ["extensions"] });
      setNotes("");
      toast({
        tone: "green",
        title: creating ? "Tool added to the assistant" : "Saved a new version",
        body: creating ? `${name} is in every conversation it’s shared with.` : undefined,
      });
      if (creating) router.push(`/extensions/${eid}/canvas`);
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t save", body: e.message }),
  });

  if (!creating && q.isLoading) return <Skeleton className="m-6 h-[420px]" />;
  if (!creating && (q.error || !base))
    return (
      <EmptyState tone="error" icon={<Wrench />} title="Couldn’t load this tool">
        {(q.error as Error | null)?.message}
      </EmptyState>
    );
  if (!creating && (base!.kind !== "tool" || spec.run?.type !== "graph"))
    return (
      <EmptyState
        icon={<Wrench />}
        title="This extension isn’t drawn on the canvas"
        actions={
          <Button asChild>
            <Link href={`/extensions/${id}`}>Open its manifest</Link>
          </Button>
        }
      />
    );

  return (
    <div className="flex min-h-[calc(100vh-64px)] flex-col">
      <header className="flex flex-wrap items-center gap-3 border-b border-border px-4 py-3.5 md:px-5">
        <nav aria-label="Breadcrumb" className="w-full text-[12px] font-medium text-fg-muted">
          <Link href="/extensions" className="hover:text-fg hover:underline">
            Extensions
          </Link>{" "}
          ›{" "}
          {creating ? (
            "New tool"
          ) : (
            <Link href={`/extensions/${id}`} className="hover:text-fg hover:underline">
              {base!.name}
            </Link>
          )}
        </nav>
        <Wrench aria-hidden className="size-5 text-fg-secondary" />
        {creating ? (
          <Input
            aria-label="Tool name"
            mono
            placeholder="name, e.g. summarise"
            value={name}
            onChange={(e) => setName(e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_"))}
            className="h-9 w-[260px]"
            autoFocus
          />
        ) : (
          <h1 className="font-mono text-[16px] font-bold text-fg">
            {base!.name} <span className="font-normal text-fg-muted">v{base!.current}</span>
          </h1>
        )}
        <span className="flex-1" />
        {!creating && (
          <Input
            aria-label="Version notes"
            placeholder="What changed (optional)"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            className="h-8 w-[200px] text-[13px]"
            disabled={!editable || !dirty}
          />
        )}
        <Button
          size="sm"
          variant="approve"
          disabled={!editable || Boolean(firstProblem) || save.isPending || !dirty}
          disabledReason={
            !editable
              ? `Only its owner (${base?.owner_email ?? "someone else"}) or an admin can change it`
              : firstProblem
                ? `Fix this first: ${firstProblem}`
                : "Nothing changed"
          }
          onClick={() => save.mutate()}
        >
          {creating ? "Add to the assistant" : "Save version"}
        </Button>
      </header>
      <GraphEditor
        key={base?.version ?? "new"}
        graph={graph}
        setGraph={setGraph}
        scope="tool"
        root="custom"
        readOnly={!editable}
        catalog={catalog.data?.node_types}
        customDefs={customDefs}
        templates={[] as TemplateSummary[]}
        fields={[]}
        emptyPanel={
          <fieldset disabled={!editable} className="flex flex-col gap-3">
            <h2 className="text-[15px] font-bold text-fg">About this tool</h2>
            <p className="text-[12.5px] text-fg-secondary">
              Its Input nodes are its parameters: the assistant fills them in when it calls the tool. What reaches its
              Return nodes is what it gives back. Ask the model, call other tools, and shape what passes between them
              with the building blocks.
            </p>
            <Field label="What it does" hint="The assistant reads this to decide when to call it">
              {({ id: fid, describedBy }) => (
                <Textarea
                  id={fid}
                  aria-describedby={describedBy}
                  rows={2}
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                />
              )}
            </Field>
            <Field label="When it runs">
              {({ id: fid }) => (
                <Select
                  id={fid}
                  value={effect}
                  onChange={(e) => setEffect(e.target.value)}
                  options={[
                    { value: "read", label: "At once: it only looks things up" },
                    { value: "change", label: "After the person approves: it changes something" },
                  ]}
                />
              )}
            </Field>
            {creating && (
              <VisibilityFields
                visibility={visibility}
                namespaces={namespaces}
                onChange={(v, ns) => {
                  setVisibility(v);
                  setNamespaces(ns);
                }}
              />
            )}
            <span className="label-caps pt-2">Parameters</span>
            {paramList.length ? (
              paramList.map((p) => (
                <div key={p.name} className="flex flex-col gap-1.5 rounded-sm border border-border p-2">
                  <div className="grid grid-cols-[1fr_130px] items-center gap-1.5">
                    <code className="truncate font-mono text-[13px] font-semibold text-fg">{p.name}</code>
                    <Select
                      aria-label={`Kind of ${p.name}`}
                      size="sm"
                      value={p.kind ?? "text"}
                      onChange={(e) => setParams({ ...params, [p.name]: { ...p, kind: e.target.value } })}
                      options={Object.entries(KINDS).map(([value, label]) => ({ value, label }))}
                    />
                  </div>
                  <Input
                    aria-label={`What ${p.name} is`}
                    placeholder="What it is, for the assistant"
                    value={p.description ?? ""}
                    onChange={(e) => setParams({ ...params, [p.name]: { ...p, description: e.target.value } })}
                  />
                  <Checkbox
                    checked={Boolean(p.required)}
                    onCheckedChange={(v) => setParams({ ...params, [p.name]: { ...p, required: v } })}
                    label="Always needed"
                  />
                </div>
              ))
            ) : (
              <p className="text-[12.5px] text-fg-muted">Add an Input node for each thing the tool needs.</p>
            )}
            {firstProblem && <p className="text-[12.5px] text-red-dark">{firstProblem}</p>}
          </fieldset>
        }
      />
    </div>
  );
}
