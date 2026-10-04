"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { Workflows } from "@/app/openapi-client";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Checkbox, Field, Input, Select, Textarea } from "@/components/ui/field";
import { ICONS, type Folded } from "@/components/workflows/graph-editor";
import { NAME_RX, kindOf, paramCandidates, type CustomDef, type WfGraph } from "@/components/workflows/workflow-model";
import { data, useApiClient } from "@/lib/api/browser";
import { useArchive } from "@/lib/hooks/session";

export const TONES = ["blue", "green", "gold", "red", "purple", "neutral"];
export const VISIBILITY: Record<string, string> = {
  private: "Only me",
  namespace: "Members of chosen namespaces",
  everyone: "Everyone signed in",
};

/** Who sees a custom node: only its owner, chosen namespaces, or everyone. */
export function VisibilityFields({
  visibility,
  namespaces,
  onChange,
}: {
  visibility: string;
  namespaces: string[];
  onChange: (visibility: string, namespaces: string[]) => void;
}) {
  const { namespaces: mine, can } = useArchive();
  const shareable = mine.filter((n) => can("editor", n.name));
  return (
    <>
      <Field label="Who can use it" hint="Only you or an admin can change it">
        {({ id, describedBy }) => (
          <Select
            id={id}
            aria-describedby={describedBy}
            value={visibility}
            onChange={(e) => onChange(e.target.value, namespaces)}
            options={Object.entries(VISIBILITY).map(([value, label]) => ({ value, label }))}
          />
        )}
      </Field>
      {visibility === "namespace" && (
        <div className="flex flex-col gap-1.5">
          {shareable.length ? (
            shareable.map((n) => (
              <Checkbox
                key={n.name}
                checked={namespaces.includes(n.name)}
                onCheckedChange={(v) =>
                  onChange(visibility, v ? [...namespaces, n.name] : namespaces.filter((x) => x !== n.name))
                }
                label={n.name}
              />
            ))
          ) : (
            <p className="text-[12.5px] text-fg-muted">You need editor access to a namespace to share with it.</p>
          )}
        </div>
      )}
    </>
  );
}

type Pick = { node: string; key: string; value: unknown; on: boolean; name: string };

/** Selected nodes saved as a custom node: its name and look, who can use it, and which settings become parameters. */
export function SaveCustomDialog({ folded, onDone }: { folded: Folded | null; onDone: (d: CustomDef | null) => void }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [icon, setIcon] = useState("puzzle");
  const [color, setColor] = useState("purple");
  const [visibility, setVisibility] = useState("private");
  const [namespaces, setNamespaces] = useState<string[]>([]);
  const [picks, setPicks] = useState<Pick[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  useEffect(() => {
    if (!folded) return;
    setName("");
    setDescription("");
    setError(null);
    setPicks(
      paramCandidates(folded.body).map((c) => ({
        ...c,
        on: false,
        name: c.key.toLowerCase().replace(/[^a-z0-9_]/g, "_"),
      })),
    );
  }, [folded]);

  const save = async () => {
    if (!folded) return;
    const on = picks.filter((p) => p.on);
    const names = on.map((p) => p.name);
    if (names.some((n) => !NAME_RX.test(n)) || new Set(names).size !== names.length) {
      setError("Name each parameter differently, with lowercase letters, digits and _.");
      return;
    }
    const body: WfGraph = {
      ...folded.body,
      nodes: folded.body.nodes.map((n) => {
        const mine = on.filter((p) => p.node === n.id);
        if (!mine.length) return n;
        return { ...n, config: { ...n.config, ...Object.fromEntries(mine.map((p) => [p.key, { $param: p.name }])) } };
      }),
    };
    const params = on.map((p) => ({ name: p.name, label: p.key, kind: kindOf(p.value), default: p.value }));
    setPending(true);
    setError(null);
    try {
      const r = await data(
        Workflows.createCustomNode({
          client,
          body: {
            name: name.trim(),
            graph: body,
            params,
            description: description.trim() || null,
            icon,
            color,
            visibility: visibility as "private" | "namespace" | "everyone",
            namespaces,
          },
        }),
      );
      const d = await data(Workflows.getCustomNode({ client, path: { nid: r.id } }));
      void qc.invalidateQueries({ queryKey: ["workflows"] });
      void qc.invalidateQueries({ queryKey: ["custom-nodes"] });
      onDone(d as unknown as CustomDef);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  };

  return (
    <Dialog
      open={folded != null}
      onOpenChange={(o) => !o && onDone(null)}
      title="Save as a custom node"
      description="The selected nodes become one node you can use again, here and in other workflows. What came in from outside becomes its inputs; what went out, its outputs."
      wide
      actions={
        <>
          <Button variant="ghost" onClick={() => onDone(null)}>
            Cancel
          </Button>
          <Button variant="primary" disabled={!name.trim() || pending} onClick={() => void save()}>
            {pending ? "Saving…" : "Save custom node"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <Field label="Name">
          {({ id }) => (
            <Input
              id={id}
              value={name}
              placeholder="e.g. Ticket numbers"
              autoFocus
              onChange={(e) => setName(e.target.value)}
            />
          )}
        </Field>
        <Field label="What it does" optional>
          {({ id }) => (
            <Textarea id={id} rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />
          )}
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Icon">
            {({ id }) => (
              <Select id={id} value={icon} onChange={(e) => setIcon(e.target.value)} options={Object.keys(ICONS)} />
            )}
          </Field>
          <Field label="Colour">
            {({ id }) => <Select id={id} value={color} onChange={(e) => setColor(e.target.value)} options={TONES} />}
          </Field>
        </div>
        <VisibilityFields
          visibility={visibility}
          namespaces={namespaces}
          onChange={(v, ns) => {
            setVisibility(v);
            setNamespaces(ns);
          }}
        />
        {picks.length > 0 && (
          <div className="flex flex-col gap-1.5">
            <span className="label-caps">Settings people can change where they use it</span>
            {picks.map((p, i) => (
              <div key={`${p.node}.${p.key}`} className="grid grid-cols-[1fr_160px] items-center gap-2">
                <Checkbox
                  checked={p.on}
                  onCheckedChange={(v) => setPicks(picks.map((x, k) => (k === i ? { ...x, on: v } : x)))}
                  label={
                    <span>
                      <code className="font-mono text-[12px]">
                        {p.node}.{p.key}
                      </code>{" "}
                      <span className="text-fg-muted">= {JSON.stringify(p.value).slice(0, 40)}</span>
                    </span>
                  }
                />
                {p.on && (
                  <Input
                    aria-label="Parameter name"
                    mono
                    value={p.name}
                    onChange={(e) => setPicks(picks.map((x, k) => (k === i ? { ...x, name: e.target.value } : x)))}
                  />
                )}
              </div>
            ))}
          </div>
        )}
        {error && (
          <p role="alert" className="text-[12.5px] text-red-dark">
            {error}
          </p>
        )}
      </div>
    </Dialog>
  );
}
