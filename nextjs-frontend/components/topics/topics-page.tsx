"use client";

import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Plus, Tags } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { Topics } from "@/app/openapi-client";
import { findTopics, topicTree, type TopicItem } from "@/components/topics/model";
import { TopicCandidates } from "@/components/topics/topic-candidates";
import { NewTopicDialog, TopicDrawer } from "@/components/topics/topic-drawer";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { SearchInput, Select } from "@/components/ui/field";
import { EmptyState, PageHeader, SkeletonRows } from "@/components/ui/states";
import { data, useApiClient } from "@/lib/api/browser";
import { count, plural } from "@/lib/format";
import { needRole, useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

/** A namespace's vocabulary of topics as a tree of broader and narrower topics, with each topic's recordings. */
export function TopicsPage() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const client = useApiClient();
  const { namespaces, namespace: topNs, can } = useArchive();
  const open = Number(params.get("topic")) || null;
  const [adding, setAdding] = useState(false);
  const [under, setUnder] = useState<number | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [q, setQ] = useState("");

  // a link to one topic (/topics/12) opens it in its own namespace
  const linked = useQuery({
    queryKey: ["topic", open],
    queryFn: () => data(Topics.getTopic({ client, path: { tid: open! } })),
    enabled: Boolean(open) && !params.get("ns"),
  });
  const wanted = params.get("ns") ?? linked.data?.namespace ?? null;
  const ns = wanted && namespaces.some((n) => n.name === wanted) ? wanted : (topNs ?? namespaces[0]?.name ?? null);

  const set = (changes: Record<string, string | null>) => {
    const p = new URLSearchParams(params.toString());
    for (const [k, v] of Object.entries(changes)) {
      if (v) p.set(k, v);
      else p.delete(k);
    }
    router.replace(`${pathname}?${p}`);
  };

  const list = useQuery({
    queryKey: ["topics", ns],
    queryFn: () => data(Topics.listTopics({ client, query: { ns: ns!, limit: 1000 } })),
    enabled: Boolean(ns),
  });
  const all = useMemo(() => (list.data?.items ?? []) as TopicItem[], [list.data]);
  const rows = useMemo(() => topicTree(all, expanded), [all, expanded]);
  const found = useMemo(() => findTopics(all, q), [all, q]);

  // open the way down to a topic picked elsewhere (a narrower topic, a link)
  useEffect(() => {
    if (!open || !all.length) return;
    const byId = new Map(all.map((t) => [t.id, t]));
    const chain: number[] = [];
    let cur = byId.get(open);
    const seen = new Set<number>();
    while (cur && cur.broader.length && !seen.has(cur.id)) {
      seen.add(cur.id);
      const up = byId.get(cur.broader[0]);
      if (!up) break;
      chain.unshift(up.id);
      cur = up;
    }
    if (!chain.length) return;
    setExpanded((e) => {
      const next = new Set(e);
      chain.forEach((_, i) => next.add(chain.slice(0, i + 1).join("/")));
      return next.size === e.size ? e : next;
    });
  }, [open, all]);

  const toggle = (path: string) =>
    setExpanded((e) => {
      const next = new Set(e);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  const editor = Boolean(ns) && can("editor", ns!);

  if (!namespaces.length)
    return (
      <div className="px-4 py-6 md:px-6">
        <PageHeader title="Topics" />
        <EmptyState icon={<Tags />} title="No namespaces yet">
          Topics are kept per namespace. Once you have access to one, its vocabulary shows here.
        </EmptyState>
      </div>
    );

  const item = (t: TopicItem, depth: number, path: string, hasChildren: boolean, isOpen: boolean) => (
    <li key={path} className="flex flex-col">
      <div
        className={cn(
          "flex h-10 items-center gap-1 rounded-sm pr-2 text-[13.5px]",
          open === t.id ? "bg-hl" : "hover:bg-surface-neutral",
        )}
        style={{ paddingLeft: 4 + depth * 20 }}
      >
        {hasChildren ? (
          <button
            type="button"
            aria-label={`${isOpen ? "Close" : "Open"} ${t.label}`}
            aria-expanded={isOpen}
            onClick={() => toggle(path)}
            className="grid size-7 shrink-0 place-items-center rounded-full text-fg-muted hover:bg-background hover:text-fg"
          >
            {isOpen ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
          </button>
        ) : (
          <span className="w-7 shrink-0" />
        )}
        <button
          type="button"
          onClick={() => set({ topic: String(t.id) })}
          className="flex h-full min-w-0 flex-1 items-center gap-2 text-left"
        >
          <span className="min-w-0 truncate font-semibold text-fg">{t.label}</span>
          {t.alt.length > 0 && (
            <span className="hidden min-w-0 truncate text-[12px] text-fg-muted sm:inline">also {t.alt.join(", ")}</span>
          )}
        </button>
        <span className="tabular shrink-0 text-[12px] text-fg-muted">
          {t.recordings ? plural(t.recordings, "recording") : ""}
        </span>
        {editor && (
          <button
            type="button"
            aria-label={`Add a topic under ${t.label}`}
            onClick={() => {
              setUnder(t.id);
              setAdding(true);
            }}
            className="grid size-7 shrink-0 place-items-center rounded-full text-fg-muted hover:bg-background hover:text-fg"
          >
            <Plus className="size-3.5" />
          </button>
        )}
      </div>
    </li>
  );

  return (
    <div className="flex flex-col gap-3 px-4 py-6 md:px-6">
      <PageHeader
        className="mb-0"
        title="Topics"
        meta={ns && list.data ? `${ns} · ${plural(list.data.total, "topic")}` : undefined}
        actions={
          <span className="flex flex-wrap items-center gap-3">
            {ns && (
              <Button
                size="sm"
                variant="primary"
                icon={<Plus />}
                disabled={!editor}
                disabledReason={!editor ? needRole("editor", ns) : undefined}
                onClick={() => {
                  setUnder(null);
                  setAdding(true);
                }}
              >
                Add topic
              </Button>
            )}
            {namespaces.length > 1 && (
              <label className="flex items-center gap-2 text-[13px] font-semibold text-fg-secondary">
                Namespace
                <Select
                  size="sm"
                  className="w-[200px]"
                  value={ns ?? ""}
                  onChange={(e) => {
                    setExpanded(new Set());
                    set({ ns: e.target.value, topic: null });
                  }}
                  options={namespaces.map((n) => n.name)}
                  aria-label="Namespace"
                />
              </label>
            )}
          </span>
        }
      />
      <p className="m-0 max-w-[720px] text-[13px] text-fg-secondary">
        What this namespace’s recordings are about, as a controlled vocabulary: each topic can sit under broader ones.
        Entities are the people, organisations and things that are named; topics are kept apart from them.
      </p>
      {ns && editor && (
        <TopicCandidates
          ns={ns}
          onAdded={(id) => {
            list.refetch();
            set({ topic: String(id) });
          }}
        />
      )}
      <SearchInput
        className="w-full sm:w-[280px]"
        placeholder="Find by label"
        aria-label="Find topics"
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
      {list.isLoading && <SkeletonRows rows={6} />}
      {list.isError && (
        <Banner
          tone="error"
          title="Couldn’t load the topics."
          action={
            <Button size="sm" variant="secondary" onClick={() => list.refetch()}>
              Try again
            </Button>
          }
        >
          {list.error.message}
        </Banner>
      )}
      {list.data && !all.length && (
        <EmptyState
          icon={<Tags />}
          title="No topics yet"
          className="rounded-lg border border-border"
          actions={
            editor ? (
              <Button size="sm" variant="primary" onClick={() => setAdding(true)}>
                Add a topic
              </Button>
            ) : undefined
          }
        >
          Add the subjects this namespace’s recordings are about. A topic-like entity (a term) can also become a topic
          from its entity page.
        </EmptyState>
      )}
      {all.length > 0 && q.trim() && (
        <div className="rounded-md border border-border p-1">
          {found.length ? (
            <ul className="m-0 flex list-none flex-col p-0" aria-label="Matching topics">
              {found.map((t) => item(t, 0, `q/${t.id}`, false, false))}
            </ul>
          ) : (
            <p className="m-0 p-3 text-[13px] text-fg-secondary">No topic matches “{q.trim()}”.</p>
          )}
        </div>
      )}
      {all.length > 0 && !q.trim() && (
        <div className="rounded-md border border-border p-1">
          <ul className="m-0 flex list-none flex-col p-0" aria-label="Topics">
            {rows.map((r) => item(r.topic, r.depth, r.path, r.hasChildren, expanded.has(r.path)))}
          </ul>
          <p className="m-0 px-3 py-2 text-[12px] text-fg-muted">
            {count(all.filter((t) => !t.broader.length).length)} top topics ·{" "}
            {plural(
              all.reduce((n, t) => n + t.recordings, 0),
              "link",
            )}{" "}
            to recordings
          </p>
        </div>
      )}
      {ns && (
        <NewTopicDialog
          ns={ns}
          all={all}
          open={adding}
          under={under}
          onOpenChange={setAdding}
          onCreated={(id) => {
            setAdding(false);
            list.refetch();
            if (under) setExpanded((e) => new Set(e));
            set({ topic: String(id) });
          }}
        />
      )}
      {open && ns && (
        <TopicDrawer
          id={open}
          ns={ns}
          all={all}
          onSelect={(id) => set({ topic: String(id) })}
          onClose={() => set({ topic: null })}
        />
      )}
    </div>
  );
}
