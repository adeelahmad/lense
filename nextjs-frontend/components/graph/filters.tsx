"use client";

import { useState } from "react";

import { EDGE_KINDS, findNodes, NODE_GROUPS, type GraphNode } from "@/components/graph/model";
import { EdgeIcon, NodeIcon, ShapeIcon } from "@/components/graph/shape";
import { Checkbox, SearchInput, Select } from "@/components/ui/field";
import { Segmented } from "@/components/ui/tabs";
import { count } from "@/lib/format";
import { cn } from "@/lib/utils";

/** "Find in graph": type to list matching nodes; Enter picks the best one. */
export function GraphFinder({ nodes, onPick }: { nodes: GraphNode[]; onPick: (id: string) => void }) {
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const hits = findNodes(nodes, q).slice(0, 8);
  return (
    <div className="relative">
      <SearchInput
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setActive(0);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setActive((a) => Math.min(hits.length - 1, a + 1));
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setActive((a) => Math.max(0, a - 1));
          } else if (e.key === "Enter" && hits[active]) {
            e.preventDefault();
            onPick(hits[active].id);
            setQ("");
          } else if (e.key === "Escape") setQ("");
        }}
        placeholder="Find in graph"
        aria-label="Find in graph"
        role="combobox"
        aria-expanded={hits.length > 0}
        aria-controls="graph-finder"
        aria-activedescendant={hits[active] ? `gf-${active}` : undefined}
        className="[&_input]:bg-background"
      />
      {q.trim() && (
        <ul
          id="graph-finder"
          role="listbox"
          className="absolute inset-x-0 top-10 z-10 m-0 list-none rounded-md border border-border bg-background p-1 shadow-2"
        >
          {hits.length === 0 && <li className="px-2.5 py-2 text-[13px] text-fg-muted">No node called that here</li>}
          {hits.map((n, i) => (
            <li key={n.id} id={`gf-${i}`} role="option" aria-selected={i === active}>
              <button
                type="button"
                onMouseMove={() => setActive(i)}
                onClick={() => {
                  onPick(n.id);
                  setQ("");
                }}
                className={cn(
                  "flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-[13px] text-fg",
                  i === active && "bg-hl",
                )}
              >
                <NodeIcon n={n} size={10} />
                <span className="truncate">{n.label}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Scope, which node and edge types show, and the minimum edge weight. */
export function GraphFilters({
  nodes,
  scope,
  ns,
  namespaces,
  onScope,
  groups,
  kinds,
  onGroups,
  onKinds,
  minWeight,
  maxWeight,
  onMinWeight,
  shown,
  total,
  maxNodes,
  onPick,
  isolated,
}: {
  nodes: GraphNode[];
  scope: "ns" | "all";
  ns: string | null;
  namespaces: string[];
  onScope: (scope: "ns" | "all", ns?: string) => void;
  groups: Set<string>;
  kinds: Set<string>;
  onGroups: (s: Set<string>) => void;
  onKinds: (s: Set<string>) => void;
  minWeight: number;
  maxWeight: number;
  onMinWeight: (n: number) => void;
  shown: number;
  total: number;
  maxNodes: number | null;
  onPick: (id: string) => void;
  isolated: string[];
}) {
  const present = new Set(nodes.map((n) => (n.kind === "speaker" ? "speaker" : (n.type ?? "TERM"))));
  const toggle = (set: Set<string>, v: string) => {
    const s = new Set(set);
    if (s.has(v)) s.delete(v);
    else s.add(v);
    return s;
  };
  return (
    <div className="flex flex-col gap-3.5">
      <GraphFinder nodes={nodes} onPick={onPick} />
      <div className="flex flex-col gap-1.5">
        <span className="label-caps">Scope</span>
        <Segmented
          className="w-full [&>button]:flex-1 [&>button]:justify-center"
          value={scope}
          onChange={(v) => onScope(v as "ns" | "all")}
          items={[
            {
              value: "ns",
              label: <span className="block max-w-[96px] truncate">{ns ?? "Namespace"}</span>,
            },
            { value: "all", label: "All shared" },
          ]}
        />
        {scope === "ns" && namespaces.length > 1 && (
          <Select
            size="sm"
            aria-label="Namespace"
            value={ns ?? ""}
            onChange={(e) => onScope("ns", e.target.value)}
            options={namespaces}
          />
        )}
        {scope === "all" && isolated.length > 0 && (
          <p className="m-0 text-[11.5px] leading-snug text-fg-muted">
            {isolated.join(", ")} keep their graph to themselves; pick them as the namespace to see it.
          </p>
        )}
      </div>
      <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
        <legend className="mb-2 label-caps">Nodes</legend>
        {NODE_GROUPS.filter(
          (g) => present.has(g.key) || g.key === "speaker" || ["ORG", "PRODUCT", "PLACE", "TERM"].includes(g.key),
        ).map((g) => (
          <Checkbox
            key={g.key}
            checked={groups.has(g.key)}
            onCheckedChange={() => onGroups(toggle(groups, g.key))}
            label={
              <span className="flex items-center gap-2 text-[13px] font-medium">
                <ShapeIcon shape={g.shape} size={11} />
                {g.label}
              </span>
            }
          />
        ))}
      </fieldset>
      <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
        <legend className="mb-2 label-caps">Edges</legend>
        {EDGE_KINDS.filter((k) => k.key !== "same thing").map((k) => (
          <Checkbox
            key={k.key}
            checked={kinds.has(k.key)}
            onCheckedChange={() => onKinds(toggle(kinds, k.key))}
            label={
              <span className="flex items-center gap-2 text-[13px] font-medium">
                <EdgeIcon kind={k.key} />
                {k.label}
              </span>
            }
          />
        ))}
      </fieldset>
      <div className="flex flex-col gap-1.5">
        <label htmlFor="min-weight" className="label-caps">
          Min edge weight · {minWeight}
        </label>
        <input
          id="min-weight"
          type="range"
          min={1}
          max={Math.max(2, maxWeight)}
          value={minWeight}
          onChange={(e) => onMinWeight(Number(e.target.value))}
          className="w-full accent-[var(--aladdin-blue)]"
        />
        <span className="text-[12px] leading-snug text-fg-muted">
          Showing {count(shown)} of {count(total)} nodes
          {maxNodes ? ` (max ${count(maxNodes)}, set in Settings)` : ""}
        </span>
      </div>
    </div>
  );
}
