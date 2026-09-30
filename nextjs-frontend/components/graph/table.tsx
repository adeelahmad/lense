"use client";

import { useMemo, useState } from "react";

import { edgeStyle, typeLabel, type GraphEdge, type GraphNode } from "@/components/graph/model";
import { NodeIcon } from "@/components/graph/shape";
import { talkTime } from "@/components/speakers/format";
import { SortTh, Table, Td, Th, THead, Tr } from "@/components/ui/table";
import { Tabs } from "@/components/ui/tabs";
import { count } from "@/lib/format";

type Sort = { key: string; dir: "asc" | "desc" };

function useSort<T>(rows: T[], get: (r: T, key: string) => string | number, initial: Sort) {
  const [sort, setSort] = useState<Sort>(initial);
  const sorted = useMemo(
    () =>
      [...rows].sort((a, b) => {
        const x = get(a, sort.key);
        const y = get(b, sort.key);
        const c = x < y ? -1 : x > y ? 1 : 0;
        return sort.dir === "asc" ? c : -c;
      }),
    [rows, sort],
  );
  const th = (key: string) => ({
    active: sort.key === key,
    dir: sort.dir,
    onSort: () =>
      setSort((s) => ({
        key,
        dir: s.key === key && s.dir === "desc" ? "asc" : "desc",
      })),
  });
  return { sorted, th };
}

/** GR2: the accessible equivalent of the canvas — every node and every edge as real tables. */
export function GraphTable({
  nodes,
  edges,
  selected,
  onSelect,
}: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const [tab, setTab] = useState<"nodes" | "edges">("edges");
  const byId = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes]);
  const degree = useMemo(() => {
    const d = new Map<string, number>();
    for (const e of edges) {
      d.set(e.a, (d.get(e.a) ?? 0) + 1);
      d.set(e.b, (d.get(e.b) ?? 0) + 1);
    }
    return d;
  }, [edges]);
  const label = (id: string) => {
    const n = byId.get(id);
    if (!n) return id;
    return n.ns.length === 1 && edges.some((e) => e.kind === "maybe the same voice" && (e.a === id || e.b === id))
      ? `${n.label} (${n.ns[0]})`
      : n.label;
  };
  const edgeSort = useSort(
    edges,
    (e, k) =>
      k === "from"
        ? label(e.a).toLowerCase()
        : k === "to"
          ? label(e.b).toLowerCase()
          : k === "type"
            ? edgeStyle(e.kind).label
            : e.w,
    { key: "weight", dir: "desc" },
  );
  const nodeSort = useSort(
    nodes,
    (n, k) =>
      k === "name"
        ? n.label.toLowerCase()
        : k === "type"
          ? typeLabel(n)
          : k === "links"
            ? (degree.get(n.id) ?? 0)
            : n.weight,
    { key: "links", dir: "desc" },
  );
  return (
    <div className="flex flex-col gap-2.5">
      <Tabs
        aria-label="Table view"
        value={tab}
        onChange={(v) => setTab(v as "nodes" | "edges")}
        items={[
          { value: "nodes", label: "Nodes", count: count(nodes.length) },
          { value: "edges", label: "Edges", count: count(edges.length) },
        ]}
      />
      {tab === "edges" ? (
        <Table aria-label="Edges">
          <THead>
            <tr>
              <SortTh {...edgeSort.th("from")}>From</SortTh>
              <SortTh {...edgeSort.th("type")}>Type</SortTh>
              <SortTh {...edgeSort.th("to")}>To</SortTh>
              <SortTh {...edgeSort.th("weight")} className="text-right">
                Weight
              </SortTh>
            </tr>
          </THead>
          <tbody>
            {edgeSort.sorted.map((e) => (
              <Tr key={`${e.a}-${e.b}`} selected={selected === e.a || selected === e.b}>
                <Td>
                  <button
                    type="button"
                    onClick={() => onSelect(e.a)}
                    className="text-left font-semibold text-fg hover:text-fg-accent hover:underline"
                  >
                    {label(e.a)}
                  </button>
                </Td>
                <Td className="text-fg-secondary">{edgeStyle(e.kind).label}</Td>
                <Td>
                  <button
                    type="button"
                    onClick={() => onSelect(e.b)}
                    className="text-left font-semibold text-fg hover:text-fg-accent hover:underline"
                  >
                    {label(e.b)}
                  </button>
                </Td>
                <Td className="tabular text-right">{count(e.w)}</Td>
              </Tr>
            ))}
          </tbody>
        </Table>
      ) : (
        <Table aria-label="Nodes">
          <THead>
            <tr>
              <SortTh {...nodeSort.th("name")}>Name</SortTh>
              <SortTh {...nodeSort.th("type")}>Type</SortTh>
              <Th>Namespace</Th>
              <SortTh {...nodeSort.th("weight")} className="text-right">
                Talk time / mentions
              </SortTh>
              <SortTh {...nodeSort.th("links")} className="text-right">
                Links
              </SortTh>
            </tr>
          </THead>
          <tbody>
            {nodeSort.sorted.map((n) => (
              <Tr key={n.id} selected={selected === n.id}>
                <Td>
                  <button
                    type="button"
                    onClick={() => onSelect(n.id)}
                    className="flex items-center gap-2 text-left font-semibold text-fg hover:text-fg-accent hover:underline"
                  >
                    <NodeIcon n={n} size={10} />
                    {n.label}
                  </button>
                </Td>
                <Td className="text-fg-secondary">{typeLabel(n)}</Td>
                <Td className="text-fg-secondary">{n.ns.join(", ")}</Td>
                <Td className="tabular text-right">
                  {n.kind === "speaker" ? talkTime(n.weight * 60000) : count(n.weight)}
                </Td>
                <Td className="tabular text-right">{count(degree.get(n.id) ?? 0)}</Td>
              </Tr>
            ))}
          </tbody>
        </Table>
      )}
    </div>
  );
}
