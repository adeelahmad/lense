"use client";

import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, Plus, Puzzle, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Fields, Workflows } from "@/app/openapi-client";
import type { TemplateSummary } from "@/app/openapi-client/types.gen";
import { useTemplateList, useWorkflowCatalog } from "@/components/pipelines/catalog-header";
import { Button, IconButton } from "@/components/ui/button";
import { Field, Input, Select, Textarea } from "@/components/ui/field";
import { Menu, MenuContent, MenuItem, MenuLabel, MenuTrigger } from "@/components/ui/menu";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { Segmented } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/toast";
import { parseValue } from "@/components/workflows/flow-settings";
import { GraphEditor, ICONS } from "@/components/workflows/graph-editor";
import type { NsField } from "@/components/workflows/node-settings";
import { TONES, VisibilityFields } from "@/components/workflows/save-custom-dialog";
import {
  NAME_RX,
  cleanGraph,
  normalize,
  problems as findProblems,
  sameGraph,
  starterBody,
  type CustomDef,
  type Scope,
  type WfGraph,
} from "@/components/workflows/workflow-model";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { relative } from "@/lib/format";
import { useArchive } from "@/lib/hooks/session";

type Param = {
  name: string;
  label?: string | null;
  kind?: string;
  default?: unknown;
  options?: unknown[] | null;
  help?: string | null;
};
type Meta = {
  name: string;
  description: string;
  icon: string;
  color: string;
  visibility: string;
  namespaces: string[];
};

const KINDS: Record<string, string> = {
  text: "Text",
  number: "Number",
  bool: "Yes or no",
  choice: "One of",
  json: "JSON",
};
const show = (v: unknown) => (v == null ? "" : typeof v === "string" ? v : JSON.stringify(v));

/** A custom node on the canvas: its body (Input nodes in, Return nodes out), its parameters, look and who can use it. */
export function CustomNodeEditor({ id }: { id?: number }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const router = useRouter();
  const toast = useToast();
  const { namespaces, can } = useArchive();
  const creating = id == null;
  const [version, setVersion] = useState<number | null>(null);
  const q = useQuery({
    queryKey: ["custom-node", id, version ?? "current"],
    queryFn: () =>
      data(Workflows.getCustomNode({ client, path: { nid: id! }, query: version ? { version } : undefined })),
    enabled: !creating,
  });
  const base = q.data as unknown as
    | (CustomDef & {
        graph: WfGraph;
        history?: { version: number; notes?: string | null; created_at?: string | null }[];
        deleted_at?: string | null;
      })
    | undefined;
  const templates = useTemplateList();
  const catalog = useWorkflowCatalog();
  const fieldLists = useQueries({
    queries: namespaces.map((n) => ({
      queryKey: ["fields", n.name],
      queryFn: () => data(Fields.listFields({ client, path: { name: n.name } })),
      staleTime: 60_000,
    })),
  });
  const fields: NsField[] = namespaces.flatMap((n, i) =>
    (fieldLists[i]?.data ?? []).map((f) => ({ ...f, namespace: n.name })),
  );
  const customDefs = (catalog.data?.custom_nodes ?? []) as unknown as CustomDef[];

  const [graph, setGraph] = useState<WfGraph>(() => starterBody("group"));
  const [params, setParams] = useState<Param[]>([]);
  const [meta, setMeta] = useState<Meta>({
    name: "",
    description: "",
    icon: "puzzle",
    color: "purple",
    visibility: "private",
    namespaces: [],
  });
  const [scope, setScope] = useState<Scope>("recording");
  const [notes, setNotes] = useState("");
  useEffect(() => {
    if (!base) return;
    setGraph(normalize(base.graph));
    setParams((base.params ?? []) as Param[]);
    setMeta({
      name: base.name,
      description: base.description ?? "",
      icon: base.icon ?? "puzzle",
      color: base.color ?? "purple",
      visibility: base.visibility ?? "private",
      namespaces: base.namespaces ?? [],
    });
    setScope(base.scopes.includes("recording") ? "recording" : "graph");
  }, [base]);

  const readOnly = creating ? !can("editor") : !base?.editable || Boolean(base?.deleted_at);
  const why = readOnly
    ? creating
      ? "Needs editor access to a namespace"
      : base?.deleted_at
        ? "This custom node was removed"
        : `Only its owner (${base?.owner_email ?? "someone else"}) or an admin can change it`
    : undefined;
  const custom = (cid: number) => customDefs.find((d) => d.id === cid);
  const problems = findProblems(
    graph,
    (t) => (templates.data as TemplateSummary[] | undefined)?.find((x) => x.id === t)?.kind,
    scope,
    "custom",
    custom,
  );
  const paramProblem = params.some((p) => !NAME_RX.test(p.name))
    ? "Name each parameter with lowercase letters, digits and _."
    : new Set(params.map((p) => p.name)).size !== params.length
      ? "Two parameters have the same name."
      : params.some((p) => p.kind === "choice" && !(p.options ?? []).length)
        ? "List the choices of a One of parameter."
        : undefined;
  const firstProblem = paramProblem ?? Object.values(problems)[0];
  const graphDirty =
    creating ||
    (base
      ? !sameGraph(normalize(base.graph), graph) || JSON.stringify(base.params ?? []) !== JSON.stringify(params)
      : false);
  const metaDirty =
    !creating &&
    base != null &&
    (meta.name !== base.name ||
      meta.description !== (base.description ?? "") ||
      meta.icon !== (base.icon ?? "puzzle") ||
      meta.color !== (base.color ?? "purple") ||
      meta.visibility !== (base.visibility ?? "private") ||
      meta.namespaces.join() !== (base.namespaces ?? []).join());

  const save = useMutation({
    mutationFn: async () => {
      const body = cleanGraph(graph);
      const vis = meta.visibility as "private" | "namespace" | "everyone";
      if (creating) {
        const r = await data(
          Workflows.createCustomNode({
            client,
            body: {
              name: meta.name.trim(),
              graph: body,
              params: params as Record<string, unknown>[],
              description: meta.description.trim() || null,
              icon: meta.icon,
              color: meta.color,
              visibility: vis,
              namespaces: meta.namespaces,
            },
          }),
        );
        return { id: r.id, version: 1 };
      }
      let v = base!.current;
      if (graphDirty)
        v = (
          await data(
            Workflows.createCustomNodeVersion({
              client,
              path: { nid: id! },
              body: { graph: body, params: params as Record<string, unknown>[], notes: notes.trim() || null },
            }),
          )
        ).version;
      if (metaDirty)
        await data(
          Workflows.updateCustomNode({
            client,
            path: { nid: id! },
            body: {
              name: meta.name.trim(),
              description: meta.description.trim() || null,
              icon: meta.icon,
              color: meta.color,
              visibility: vis,
              namespaces: meta.namespaces,
            },
          }),
        );
      return { id: id!, version: v };
    },
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: ["workflows"] });
      void qc.invalidateQueries({ queryKey: ["custom-node", r.id] });
      setNotes("");
      setVersion(null);
      if (creating) {
        toast({ tone: "green", title: "Custom node saved", body: meta.name });
        router.push(`/workflows/nodes/${r.id}`);
        return;
      }
      toast({
        tone: "green",
        title: graphDirty ? `Saved v${r.version}` : "Saved",
        body: graphDirty ? "Workflows that use it keep their version until they’re moved on to this one." : undefined,
      });
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t save", body: e.message }),
  });
  const remove = useMutation({
    mutationFn: () => data(Workflows.deleteCustomNode({ client, path: { nid: id! } })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["workflows"] });
      toast({
        tone: "green",
        title: "Custom node removed",
        body: "Workflows that use it keep running the version they have.",
      });
      router.push("/workflows/nodes");
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t remove it", body: e.message }),
  });

  if (!creating && q.isLoading)
    return (
      <div className="flex flex-col gap-4 p-6" aria-busy="true" aria-label="Loading custom node">
        <Skeleton className="h-7 w-72" />
        <Skeleton className="h-[420px] w-full rounded-md" />
      </div>
    );
  if (!creating && (q.error || !base)) {
    const e = q.error as ApiError | null;
    return (
      <EmptyState
        tone={e?.status === 404 ? "neutral" : "error"}
        icon={<Puzzle />}
        title={
          e?.status === 404
            ? "This custom node doesn’t exist, or isn’t shared with you"
            : "Couldn’t load the custom node"
        }
        actions={
          <Button asChild variant="secondary">
            <Link href="/workflows/nodes">All custom nodes</Link>
          </Button>
        }
      >
        {e?.message}
      </EmptyState>
    );
  }

  const putParam = (k: number, p: Param | null) =>
    setParams(p ? params.map((x, i) => (i === k ? p : x)) : params.filter((_, i) => i !== k));
  const Icon = ICONS[meta.icon] ?? Puzzle;

  return (
    <div className="flex min-h-[calc(100vh-64px)] flex-col">
      <header className="flex flex-wrap items-center gap-3 border-b border-border px-4 py-3.5 md:px-5">
        <nav aria-label="Breadcrumb" className="w-full text-[12px] font-medium text-fg-muted">
          <Link href="/workflows/nodes" className="hover:text-fg hover:underline">
            Custom nodes
          </Link>{" "}
          › {creating ? "New custom node" : base?.name}
        </nav>
        <Icon aria-hidden className="size-5 text-fg-secondary" />
        <Input
          aria-label="Custom node name"
          placeholder="Name, e.g. Ticket numbers"
          value={meta.name}
          onChange={(e) => setMeta({ ...meta, name: e.target.value })}
          className="h-9 w-[300px] text-[16px] font-bold"
          disabled={readOnly}
          autoFocus={creating}
        />
        <Segmented
          label="Its nodes"
          value={scope}
          onChange={(v) => setScope(v as Scope)}
          items={[
            { value: "recording", label: "For recordings" },
            { value: "graph", label: "For the graph" },
          ]}
        />
        <span className="flex-1" />
        {!creating && (base?.history?.length ?? 0) > 1 && (
          <Menu>
            <MenuTrigger asChild>
              <Button size="sm" variant="ghost">
                Versions <ChevronDown />
              </Button>
            </MenuTrigger>
            <MenuContent align="end" className="w-[300px]">
              <MenuLabel>Open a version</MenuLabel>
              {(base?.history ?? []).map((h) => (
                <MenuItem
                  key={h.version}
                  onSelect={() => setVersion(h.version === base?.current ? null : h.version)}
                  shortcut={relative(h.created_at)}
                >
                  v{h.version}
                  {h.version === base?.current ? " · latest" : ""}
                  {h.notes ? ` · ${h.notes}` : ""}
                </MenuItem>
              ))}
            </MenuContent>
          </Menu>
        )}
        {!creating && (
          <Input
            aria-label="Version notes"
            placeholder="What changed (optional)"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            className="h-8 w-[200px] text-[13px]"
            disabled={readOnly || !graphDirty}
          />
        )}
        {!creating && base?.editable && !base.deleted_at && (
          <Button
            size="sm"
            variant="ghost"
            disabled={remove.isPending}
            onClick={() => {
              if (window.confirm(`Remove ${base.name}? Workflows that use it keep running the version they have.`))
                remove.mutate();
            }}
          >
            Remove
          </Button>
        )}
        <Button
          size="sm"
          variant="approve"
          disabled={
            Boolean(why) ||
            Boolean(firstProblem) ||
            save.isPending ||
            !meta.name.trim() ||
            (!creating && !graphDirty && !metaDirty)
          }
          disabledReason={
            why ??
            (firstProblem
              ? `Fix this first: ${firstProblem}`
              : !meta.name.trim()
                ? "Give it a name"
                : "Nothing changed")
          }
          onClick={() => save.mutate()}
        >
          {creating
            ? "Save custom node"
            : graphDirty
              ? `Save v${Math.max(base!.current, ...(base?.history ?? []).map((h) => h.version)) + 1}`
              : "Save"}
        </Button>
      </header>
      {why && !creating && (
        <p className="border-b border-border bg-surface px-5 py-2 text-[13px] text-fg-secondary">{why}.</p>
      )}

      <GraphEditor
        key={`${base?.version ?? "new"}-${scope}`}
        graph={graph}
        setGraph={setGraph}
        scope={scope}
        root="custom"
        readOnly={readOnly}
        catalog={catalog.data?.node_types}
        customDefs={customDefs}
        selfId={id}
        templates={(templates.data ?? []) as TemplateSummary[]}
        fields={fields}
        params={params.map((p) => p.name).filter(Boolean)}
        emptyPanel={
          <fieldset disabled={readOnly} className="flex flex-col gap-3">
            <h2 className="text-[15px] font-bold text-fg">About this custom node</h2>
            <p className="text-[12.5px] text-fg-secondary">
              Its Input nodes are its inputs where it’s used, its Return nodes its outputs. Settings you bind to a
              parameter (select a node, then Set by a parameter) are filled in by the people who use it.
            </p>
            <Field label="What it does" optional>
              {({ id: fid }) => (
                <Textarea
                  id={fid}
                  rows={2}
                  value={meta.description}
                  onChange={(e) => setMeta({ ...meta, description: e.target.value })}
                />
              )}
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Icon">
                {({ id: fid }) => (
                  <Select
                    id={fid}
                    value={meta.icon}
                    onChange={(e) => setMeta({ ...meta, icon: e.target.value })}
                    options={Object.keys(ICONS)}
                  />
                )}
              </Field>
              <Field label="Colour">
                {({ id: fid }) => (
                  <Select
                    id={fid}
                    value={meta.color}
                    onChange={(e) => setMeta({ ...meta, color: e.target.value })}
                    options={TONES}
                  />
                )}
              </Field>
            </div>
            <VisibilityFields
              visibility={meta.visibility}
              namespaces={meta.namespaces}
              onChange={(visibility, ns) => setMeta({ ...meta, visibility, namespaces: ns })}
            />
            <span className="label-caps pt-2">Parameters</span>
            {params.map((p, k) => (
              <div key={k} className="flex flex-col gap-1.5 rounded-sm border border-border p-2">
                <div className="grid grid-cols-[1fr_110px_auto] items-center gap-1.5">
                  <Input
                    aria-label="Parameter name"
                    mono
                    value={p.name}
                    placeholder="name"
                    onChange={(e) =>
                      putParam(k, { ...p, name: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_") })
                    }
                  />
                  <Select
                    aria-label="Kind"
                    size="sm"
                    value={p.kind ?? "text"}
                    onChange={(e) =>
                      putParam(k, {
                        ...p,
                        kind: e.target.value,
                        default: e.target.value === "bool" ? false : e.target.value === "number" ? 0 : "",
                      })
                    }
                    options={Object.entries(KINDS).map(([value, label]) => ({ value, label }))}
                  />
                  <IconButton label="Remove this parameter" size={30} onClick={() => putParam(k, null)}>
                    <Trash2 />
                  </IconButton>
                </div>
                <Input
                  aria-label="Label"
                  value={p.label ?? ""}
                  placeholder="Label people see"
                  onChange={(e) => putParam(k, { ...p, label: e.target.value })}
                />
                {p.kind === "choice" && (
                  <Input
                    aria-label="Choices"
                    mono
                    defaultValue={(p.options ?? []).map(show).join(", ")}
                    placeholder="choices, separated by commas"
                    onChange={(e) => {
                      const options = e.target.value
                        .split(",")
                        .map((x) => x.trim())
                        .filter(Boolean);
                      putParam(k, {
                        ...p,
                        options,
                        default: options.includes(String(p.default)) ? p.default : options[0],
                      });
                    }}
                  />
                )}
                <Input
                  aria-label="Default"
                  mono
                  defaultValue={show(p.default)}
                  placeholder="default"
                  onChange={(e) =>
                    putParam(k, {
                      ...p,
                      default:
                        p.kind === "number" || p.kind === "bool"
                          ? parseValue(e.target.value)
                          : p.kind === "json"
                            ? (() => {
                                try {
                                  return JSON.parse(e.target.value);
                                } catch {
                                  return e.target.value;
                                }
                              })()
                            : e.target.value,
                    })
                  }
                />
              </div>
            ))}
            <Button
              size="sm"
              variant="ghost"
              icon={<Plus />}
              onClick={() => setParams([...params, { name: `param${params.length + 1}`, kind: "text", default: "" }])}
            >
              Add a parameter
            </Button>
            {paramProblem && <p className="text-[12.5px] text-red-dark">{paramProblem}</p>}
          </fieldset>
        }
      />
    </div>
  );
}
