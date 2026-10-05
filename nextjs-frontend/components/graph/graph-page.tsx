"use client";

import { useQuery } from "@tanstack/react-query";
import { SlidersHorizontal, Waypoints } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Admin, Entities, Search } from "@/app/openapi-client";
import { GraphCanvas } from "@/components/graph/canvas";
import { usePositions, useExplorer } from "@/components/graph/explorer";
import { ExplorerBar, NodeMenu } from "@/components/graph/explorer-ui";
import { GraphAsk } from "@/components/graph/graph-ask";
import { GraphFilters } from "@/components/graph/filters";
import {
  EDGE_KINDS,
  filterGraph,
  findFocus,
  mergeGraph,
  NODE_GROUPS,
  summarize,
  type GraphData,
  type GraphNode,
} from "@/components/graph/model";
import { NodePanel } from "@/components/graph/panel";
import { GraphTable } from "@/components/graph/table";
import { VersionPicker } from "@/components/graph/version-picker";
import { useSpeakerDirectory } from "@/components/search/data";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Dialog, Drawer } from "@/components/ui/dialog";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { Segmented } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { useJobs } from "@/lib/hooks/jobs";
import { plural } from "@/lib/format";
import { useArchive } from "@/lib/hooks/session";

const DEFAULT_KINDS = new Set(EDGE_KINDS.map((k) => k.key).filter((k) => k !== "maybe the same voice"));
const ALL_GROUPS = new Set(NODE_GROUPS.map((g) => g.key));

/** True at 1280px and up, where the node panel has its own column (below that it opens as a drawer). */
function useWide(): boolean {
  const [wide, setWide] = useState(true);
  useEffect(() => {
    const m = window.matchMedia("(min-width: 1280px)");
    const on = () => setWide(m.matches);
    on();
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, []);
  return wide;
}

function focusKey(n: GraphNode): string {
  return n.kind === "entity" && !/^e\d+$/.test(n.id) ? `e${n.refs[0]}` : n.id;
}

/** GR1–GR2: speakers and entities as a graph (or tables), with filters, a node panel and `?focus=e12` / `s4` links. */
export function GraphPage() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const client = useApiClient();
  const { namespaces, namespace: topNs, admin } = useArchive();
  const nsNames = namespaces.map((n) => n.name);
  const focusParam = params.get("focus");
  const urlNs = params.get("ns");
  const scopeMode: "ns" | "all" = params.get("scope") === "all" ? "all" : urlNs ? "ns" : topNs ? "ns" : "all";
  const ns =
    urlNs && nsNames.includes(urlNs)
      ? urlNs
      : (topNs ?? namespaces.find((n) => n.graph !== "isolated")?.name ?? nsNames[0] ?? null);
  const scope = scopeMode === "all" || !ns ? "global" : `ns:${ns}`;
  const view = params.get("view") === "table" ? "table" : "graph";
  const asOf = params.get("as_of");

  const [groups, setGroups] = useState<Set<string>>(ALL_GROUPS);
  const [kinds, setKinds] = useState<Set<string>>(DEFAULT_KINDS);
  const [minWeight, setMinWeight] = useState(1);
  const [selected, setSelected] = useState<string | null>(null);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [missing, setMissing] = useState<string | null>(null);
  const wide = useWide();
  const tried = useRef<string | null>(null);

  const set = (patch: Record<string, string | null>) => {
    const p = new URLSearchParams(params.toString());
    for (const [k, v] of Object.entries(patch)) {
      if (v) p.set(k, v);
      else p.delete(k);
    }
    router.replace(`${pathname}?${p}`);
  };

  const graph = useQuery({
    queryKey: ["graph", scope, asOf],
    queryFn: async () =>
      (await data(Search.getGraph({ client, query: { scope, as_of: asOf } }))) as unknown as GraphData,
    staleTime: 60_000,
  });
  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: () => data(Admin.getSettings({ client })),
    enabled: admin,
    staleTime: 60_000,
  });
  const maxNodes = ((settings.data?.graph as { values?: { max_nodes?: number } } | undefined)?.values?.max_nodes ??
    null) as number | null;
  const dir = useSpeakerDirectory(Boolean(focusParam?.startsWith("s")));
  const jobs = useJobs({ status: "queued,running", limit: 100 });

  const toast = useToast();
  const onError = useCallback((m: string) => toast({ title: "Couldn’t explore that", body: m, tone: "red" }), [toast]);
  const ex = useExplorer(scope, onError, asOf);
  const [menu, setMenu] = useState<{ id: string; at: { x: number; y: number } } | null>(null);
  const overview = useMemo(() => graph.data ?? { scope, namespaces: [], nodes: [], edges: [] }, [graph.data, scope]);
  // the overview plus what was found by exploring, less what was hidden
  const all = useMemo(() => {
    const m = mergeGraph(overview, ex.extra);
    if (!ex.hidden.size) return { ...overview, ...m };
    return {
      ...overview,
      nodes: m.nodes.filter((n) => !ex.hidden.has(n.id)),
      edges: m.edges.filter((e) => !ex.hidden.has(e.a) && !ex.hidden.has(e.b)),
    };
  }, [overview, ex.extra, ex.hidden]);
  const byId = useMemo(() => new Map(all.nodes.map((n) => [n.id, n])), [all.nodes]);
  const visible = useMemo(() => filterGraph(all, { groups, kinds, minWeight }), [all, groups, kinds, minWeight]);
  const positions = usePositions(
    visible.nodes,
    visible.edges,
    ex.layout,
    ex.root ?? selected,
    ex.routePath?.nodes ?? ex.route,
    ex.pins,
  );
  const maxWeight = Math.min(
    50,
    Math.max(
      1,
      ...all.edges
        .filter((e) => e.kind === "mentions" || e.kind === "mentioned together" || e.kind === "together")
        .map((e) => e.w),
    ),
  );
  const scopeLabel = scope === "global" ? "all shared namespaces" : (ns ?? "");
  const summary = summarize(visible.nodes, visible.edges, scopeLabel);
  const node = selected ? all.nodes.find((n) => n.id === selected) : undefined;

  // ?focus=e12 or s4 selects that node; if it lives in another namespace, switch to it once.
  useEffect(() => {
    if (!focusParam || !graph.data) return;
    // nodes found by exploring or asking count too
    const n = findFocus(all.nodes, focusParam);
    if (n) {
      setSelected(n.id);
      setMissing(null);
      return;
    }
    if (!/^[es]\d+$/.test(focusParam)) return; // a recording, collection or namespace link: nothing to look up
    if (focusParam.startsWith("s") && dir.isLoading) return; // wait for the speakers to know their namespace
    if (tried.current === `${focusParam}|${scope}`) return setMissing(focusParam);
    tried.current = `${focusParam}|${scope}`;
    (async () => {
      let home: string | null | undefined;
      const e = /^e(\d+)$/.exec(focusParam);
      const s = /^s(\d+)$/.exec(focusParam);
      try {
        if (e) home = (await data(Entities.getEntity({ client, path: { eid: Number(e[1]) } }))).namespace;
        else if (s) home = dir.speakers.find((x) => x.id === Number(s[1]))?.namespace;
      } catch {
        home = null;
      }
      if (home && scope !== `ns:${home}`) set({ ns: home, scope: null });
      else setMissing(focusParam);
    })();
  }, [focusParam, graph.data, all.nodes, scope, dir.speakers.length, dir.isLoading]);

  const select = (id: string | null) => {
    // picking a node while a path is being looked for ends the path there
    const from = ex.pathStart ? byId.get(ex.pathStart) : null;
    const to = id ? byId.get(id) : null;
    if (from && to && from.id !== to.id) {
      void ex.paths(from, to);
      return;
    }
    setSelected(id);
    const n = id ? all.nodes.find((x) => x.id === id) : null;
    set({ focus: n ? focusKey(n) : null });
  };

  const waiting = namespaces
    .filter((n) => (scope === "global" ? n.graph !== "isolated" : n.name === ns))
    .reduce((a, n) => a + Math.max(0, ((n.recordings as number) ?? 0) - ((n.analyzed as number) ?? 0)), 0);
  const analyzing = (jobs.data?.jobs ?? []).filter(
    (j) =>
      j.status === "running" &&
      (j.next_step === "analyze" ||
        (j.steps ?? []).some((s) => (typeof s === "string" ? s : (s as { type?: string })?.type) === "analyze")),
  ).length;

  const filters = (
    <GraphFilters
      nodes={all.nodes}
      scope={scope === "global" ? "all" : "ns"}
      ns={ns}
      namespaces={nsNames}
      onScope={(m, name) => {
        setSelected(null);
        set(m === "all" ? { scope: "all", focus: null } : { scope: null, ns: name ?? ns, focus: null });
      }}
      groups={groups}
      kinds={kinds}
      onGroups={setGroups}
      onKinds={setKinds}
      minWeight={minWeight}
      maxWeight={maxWeight}
      onMinWeight={setMinWeight}
      shown={visible.nodes.length}
      total={all.nodes.length}
      maxNodes={maxNodes}
      onPick={(id) => select(id)}
      isolated={namespaces.filter((n) => n.graph === "isolated").map((n) => n.name)}
    />
  );
  const viewSwitch = (
    <Segmented
      className="w-full [&>button]:flex-1 [&>button]:justify-center"
      value={view}
      onChange={(v) => set({ view: v === "table" ? "table" : null })}
      items={[
        { value: "graph", label: "Graph" },
        { value: "table", label: "Table" },
      ]}
    />
  );

  const empty = graph.data && all.nodes.length === 0;
  return (
    <div className="grid h-[calc(100dvh-4rem)] grid-cols-1 lg:grid-cols-[250px_minmax(0,1fr)] xl:grid-cols-[250px_minmax(0,1fr)_320px]">
      <h1 className="sr-only">Graph</h1>
      <aside
        aria-label="Graph filters"
        className="hidden min-h-0 flex-col gap-4 overflow-y-auto border-r border-border bg-surface p-4 lg:flex"
      >
        {filters}
        <span className="flex-1" />
        <Link href="/routines/changes" className="text-[12.5px] font-semibold text-fg-accent hover:underline">
          Proposed changes to review
        </Link>
        {viewSwitch}
      </aside>

      <section
        aria-label={view === "table" ? "Graph as tables" : "Graph"}
        className="relative flex min-h-0 min-w-0 flex-col"
      >
        <div className="flex items-center gap-2 border-b border-border px-3 py-2 lg:hidden">
          <Button
            size="sm"
            variant="ghost"
            className="border-border"
            icon={<SlidersHorizontal />}
            onClick={() => setFiltersOpen(true)}
          >
            Filters
          </Button>
          <span className="flex-1" />
          <div className="w-44">{viewSwitch}</div>
        </div>
        {missing && (
          <Banner tone="warning" className="m-3 mb-0" onDismiss={() => setMissing(null)}>
            That {missing.startsWith("s") ? "speaker" : "entity"} isn’t in this graph. It may have too few links to
            show, or be hidden.
          </Banner>
        )}
        {graph.isLoading && (
          <div className="flex flex-1 items-center justify-center p-10" aria-busy="true" aria-label="Loading the graph">
            <Skeleton className="size-64 rounded-full" />
          </div>
        )}
        {graph.isError && (
          <div className="p-6">
            <Banner
              tone="error"
              title="Couldn’t load the graph."
              action={
                <Button size="sm" variant="secondary" onClick={() => graph.refetch()}>
                  Try again
                </Button>
              }
            >
              {graph.error.message}
            </Banner>
          </div>
        )}
        {empty && (
          <EmptyState
            icon={<Waypoints />}
            title={`No graph for ${scopeLabel} yet`}
            className="my-auto"
            actions={
              waiting > 0 || analyzing > 0 ? (
                <Link href="/activity" className="text-[13px] font-bold text-fg-accent hover:underline">
                  See jobs
                </Link>
              ) : (
                <Link href="/import" className="text-[13px] font-bold text-fg-accent hover:underline">
                  Import recordings or documents
                </Link>
              )
            }
          >
            The graph is built by the Analyze step.
            {waiting > 0
              ? ` ${plural(waiting, "recording")} ${waiting === 1 ? "is" : "are"} waiting for it`
              : " Nothing is waiting for it"}
            {analyzing > 0 ? ` — ${analyzing} ${analyzing === 1 ? "is" : "are"} being analyzed now.` : "."}
          </EmptyState>
        )}
        {graph.data && !empty && view === "graph" && (
          <div className="relative flex min-h-0 flex-1 flex-col">
            <GraphCanvas
              className="flex-1"
              nodes={visible.nodes}
              edges={visible.edges}
              positions={positions}
              selected={selected}
              onSelect={select}
              onMove={ex.pin}
              onMenu={(id, at) => setMenu({ id, at })}
              highlight={ex.highlight}
              route={ex.routePath}
              summary={summary}
            />
            <ExplorerBar
              ex={ex}
              byId={byId}
              asOf={asOf}
              onNow={() => set({ as_of: null })}
              version={
                <VersionPicker
                  asOf={asOf}
                  namespace={scope === "global" ? null : ns}
                  onChange={(v) => set({ as_of: v })}
                />
              }
            />
            <GraphAsk ex={ex} scope={scope} asOf={asOf} onSelect={(id) => select(id)} />
            <NodeMenu
              node={menu ? (byId.get(menu.id) ?? null) : null}
              at={menu?.at ?? null}
              ex={ex}
              byId={byId}
              onClose={() => setMenu(null)}
              onSelect={(id) => select(id)}
            />
          </div>
        )}
        {graph.data && !empty && view === "table" && (
          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 md:px-5">
            <p className="m-0 mb-2 text-[13px] text-fg-secondary">{summary}</p>
            <GraphTable nodes={visible.nodes} edges={visible.edges} selected={selected} onSelect={(id) => select(id)} />
          </div>
        )}
      </section>

      <aside
        aria-label="Selected node"
        className="hidden min-h-0 overflow-y-auto border-l border-border px-[18px] py-4 xl:block"
      >
        {node ? (
          <NodePanel node={node} nodes={all.nodes} edges={all.edges} onSelect={select} onClose={() => select(null)} />
        ) : (
          <div className="flex flex-col gap-2 text-[13px] leading-snug text-fg-secondary">
            <h2 className="text-[15px] font-bold text-fg">Nothing selected</h2>
            <p className="m-0">
              Pick a node to see where it’s mentioned and what it’s connected to. Tab into the graph and use the arrow
              keys, or use the Table view.
            </p>
          </div>
        )}
      </aside>

      <Drawer
        open={Boolean(node) && !wide}
        onOpenChange={(o) => !o && select(null)}
        title={node?.label ?? "Node"}
        width={360}
      >
        <div className="p-4">
          {node && (
            <NodePanel node={node} nodes={all.nodes} edges={all.edges} onSelect={select} onClose={() => select(null)} />
          )}
        </div>
      </Drawer>
      <Dialog open={filtersOpen} onOpenChange={setFiltersOpen} title="Graph filters">
        {filters}
      </Dialog>
    </div>
  );
}
