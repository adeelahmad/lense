"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { EyeOff, FolderTree, GitMerge, Link2, Tags, Undo2, Waypoints } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Routines } from "@/app/openapi-client";
import type { GraphChange } from "@/app/openapi-client/types.gen";
import { useGraphChanges } from "@/components/routines/data";
import { RoutinesHeader } from "@/components/routines/routines-page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DateTime, EmptyState, SkeletonRows } from "@/components/ui/states";
import { Segmented } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { needRole, useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

type Side = {
  id: number;
  name: string;
  type: string;
  namespace?: string | null;
  mentions?: number;
  samples?: string[];
};
type Topic = { id: number; name: string; recordings?: number };
type Verdict = {
  same?: boolean;
  agree?: boolean;
  confidence?: number;
  keep?: "a" | "b" | null;
  type?: string;
  label?: string;
  leave?: number[];
  why?: string;
};

const pct = (n: number | null | undefined) => (n == null ? "—" : `${Math.round(n * 100)}%`);

const TYPE_NAMES: Record<string, string> = {
  PERSON: "Person",
  ORG: "Organisation",
  PRODUCT: "Product",
  PLACE: "Place",
  EVENT: "Event",
  WORK: "Work",
  TERM: "Term",
  DATE: "Date",
  NUMBER: "Number",
};
/** "Organisation" for ORG; a namespace's own CLIENT_TEAM reads "Client team". */
export const typeName = (code: string | null | undefined) =>
  code ? (TYPE_NAMES[code] ?? (code.charAt(0) + code.slice(1).toLowerCase()).replace(/_/g, " ")) : "—";

/** A group's broader topic and the topics to go under it (those the model would leave out struck through). */
function TopicGroup({ parent, topics, left }: { parent: Side; topics: Topic[]; left: Set<number> }) {
  return (
    <div className="flex flex-col gap-1.5 rounded-sm border border-border bg-background p-3">
      <p className="text-[12px] text-fg-muted">
        {parent.id ? (
          <Link href={`/topics/${parent.id}`} className="font-bold text-fg hover:text-fg-accent hover:underline">
            {parent.name}
          </Link>
        ) : (
          <span className="font-bold text-fg">{parent.name}</span>
        )}{" "}
        · {parent.namespace ?? "—"} · {topics.length} topic{topics.length === 1 ? "" : "s"} under it
      </p>
      <ul className="flex flex-wrap gap-1.5">
        {topics.map((t) => (
          <li key={t.id}>
            <Link
              href={`/topics/${t.id}`}
              className={cn(
                "rounded-sm border border-border px-1.5 py-0.5 text-[12.5px] text-fg hover:text-fg-accent",
                left.has(t.id) && "text-fg-muted line-through",
              )}
            >
              {t.name}
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** What a change is about, in a few words, and its icon. */
function heading(ch: GraphChange): { icon: typeof GitMerge; title: string } {
  const a = ch.a as Side;
  if (ch.kind === "merge") return { icon: GitMerge, title: "Merge: the same thing in one namespace?" };
  if (ch.kind === "link") return { icon: Link2, title: "Link: the same thing across namespaces?" };
  if (ch.kind === "retype") return { icon: Tags, title: `Type: ${typeName(a.type)} → ${typeName(ch.to)}?` };
  if (ch.kind === "cluster")
    return { icon: FolderTree, title: `Group under ${a.id ? "" : "a new topic, "}“${a.name}”?` };
  return { icon: EyeOff, title: "Hide as noise?" };
}

function EntitySide({ side, kept, choose }: { side: Side; kept?: boolean; choose?: () => void }) {
  return (
    <div
      className={cn(
        "flex min-w-0 flex-1 flex-col gap-1.5 rounded-sm border bg-background p-3",
        kept ? "border-blue" : "border-border",
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Link
          href={`/entities/${side.id}`}
          className="min-w-0 truncate text-[14.5px] font-bold text-fg hover:text-fg-accent hover:underline"
        >
          {side.name}
        </Link>
        <Badge mono>{side.type}</Badge>
        {kept && <Badge tone="intent">Kept</Badge>}
      </div>
      <p className="text-[12px] text-fg-muted">
        {side.namespace ?? "—"} · {side.mentions ?? 0} mention{side.mentions === 1 ? "" : "s"}
      </p>
      {side.samples?.length ? (
        <ul className="flex flex-col gap-1 text-[12.5px] text-fg-secondary">
          {side.samples.map((s, i) => (
            <li key={i} className="line-clamp-2 border-l-2 border-border pl-2">
              “{s}”
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-[12.5px] text-fg-muted">No lines to show.</p>
      )}
      {choose && (
        <Button size="xs" variant={kept ? "ghost" : "link"} className="self-start" disabled={kept} onClick={choose}>
          {kept ? "This name is kept" : "Keep this name"}
        </Button>
      )}
    </div>
  );
}

/** One change: what it is, the entities it touches, what the rules and the model said, and what to do with it. */
export function ChangeCard({ ch }: { ch: GraphChange }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const { can } = useArchive();
  const a = ch.a as Side;
  const b = (ch.b ?? null) as Side | null;
  const v = (ch.verdict ?? null) as Verdict | null;
  const merge = ch.kind === "merge";
  const pair = b != null;
  const [keep, setKeep] = useState<number>(ch.keep ?? (v?.keep === "b" && b ? b.id : a.id));
  const editor = can("editor", a.namespace) && (!b || can("editor", b.namespace));
  const why = editor ? undefined : needRole("editor", a.namespace ?? b?.namespace);
  const { icon: Icon, title } = heading(ch);
  const kept = keep === a.id || !b ? a.name : b.name;

  const act = useMutation({
    mutationFn: (what: "accept" | "dismiss" | "undo") => {
      const path = { cid: ch.id };
      if (what === "accept") return data(Routines.acceptGraphChange({ client, path, body: merge ? { keep } : {} }));
      if (what === "dismiss") return data(Routines.dismissGraphChange({ client, path }));
      return data(Routines.undoGraphChange({ client, path }));
    },
    onSuccess: (_, what) => {
      void qc.invalidateQueries({ queryKey: ["graph-changes"] });
      void qc.invalidateQueries({ queryKey: ["graph"] });
      const made = {
        merge: `Merged into ${kept}`,
        link: "Linked",
        retype: `${a.name}: ${typeName(ch.to)} now`,
        hide: `${a.name} is hidden`,
        cluster: `Grouped under ${v?.label ?? a.name}`,
      };
      toast({
        tone: "green",
        title:
          what === "accept"
            ? made[ch.kind]
            : what === "dismiss"
              ? pair
                ? "Marked as different"
                : "Left as it is"
              : "Change undone",
        body:
          what === "dismiss"
            ? pair
              ? "They won’t be suggested again."
              : "It won’t be suggested again."
            : b
              ? `${a.name} · ${b.name}`
              : a.name,
      });
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t do that", body: e.message }),
  });

  return (
    <article
      aria-label={b ? `${a.name} and ${b.name}` : a.name}
      className="flex flex-col gap-3 rounded-md border border-border bg-surface p-4"
    >
      <header className="flex flex-wrap items-center gap-2 text-[13px]">
        <Icon aria-hidden className="size-4 text-fg-secondary" />
        <span className="font-bold text-fg">{title}</span>
        <span className="flex-1" />
        <span className="text-[12px] text-fg-muted">
          {ch.status === "proposed" ? "Proposed" : ch.status === "applied" ? "Made" : ch.status}{" "}
          <DateTime iso={ch.decided_at ?? ch.created_at} />
          {ch.decided_by ? ` by ${ch.decided_by}` : ""}
        </span>
      </header>
      {ch.kind === "cluster" ? (
        <TopicGroup parent={a} topics={(ch.topics ?? []) as Topic[]} left={new Set(v?.leave ?? [])} />
      ) : (
        <div className="flex flex-col gap-3 md:flex-row">
          <EntitySide
            side={a}
            kept={merge && (ch.status === "proposed" ? keep === a.id : ch.keep === a.id)}
            choose={merge && ch.status === "proposed" && editor ? () => setKeep(a.id) : undefined}
          />
          {b && (
            <EntitySide
              side={b}
              kept={merge && (ch.status === "proposed" ? keep === b.id : ch.keep === b.id)}
              choose={merge && ch.status === "proposed" && editor ? () => setKeep(b.id) : undefined}
            />
          )}
        </div>
      )}
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[12.5px]">
        <dt className="text-fg-muted">Rule</dt>
        <dd className="text-fg">
          {ch.reason ?? "—"} · {pct(ch.confidence)}
        </dd>
        <dt className="text-fg-muted">Model</dt>
        <dd className="text-fg">
          {v ? (
            <>
              <span
                className={cn("font-semibold", (v.same ?? v.agree) === false ? "text-red-dark" : "text-green-dark")}
              >
                {pair
                  ? v.same === false
                    ? "Different"
                    : "Same"
                  : ch.kind === "retype"
                    ? v.agree === false
                      ? `Keep ${typeName(a.type)}`
                      : typeName(v.type ?? ch.to)
                    : ch.kind === "cluster"
                      ? v.agree === false
                        ? "Not together"
                        : v.label
                          ? `Together, as “${v.label}”`
                          : "Together"
                      : v.agree === false
                        ? "Not noise"
                        : "Noise"}
              </span>{" "}
              · {pct(v.confidence)}
              {v.why ? ` · ${v.why}` : ""}
            </>
          ) : (
            "Not asked"
          )}
        </dd>
      </dl>
      {ch.status === "proposed" && (
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            variant="approve"
            disabled={!editor || act.isPending}
            disabledReason={why}
            onClick={() => act.mutate("accept")}
          >
            {
              {
                merge: `Merge, keep “${kept}”`,
                link: "Link them",
                retype: `Make it ${typeName(ch.to)}`,
                hide: "Hide it",
                cluster: "Group them",
              }[ch.kind]
            }
          </Button>
          <Button
            size="sm"
            variant="secondary"
            disabled={!editor || act.isPending}
            disabledReason={why}
            onClick={() => act.mutate("dismiss")}
          >
            {pair ? "These are different" : ch.kind === "cluster" ? "Leave them" : "Leave it"}
          </Button>
        </div>
      )}
      {ch.status === "applied" && (
        <div>
          <Button
            size="sm"
            variant="secondary"
            icon={<Undo2 />}
            disabled={!editor || act.isPending}
            disabledReason={why}
            onClick={() => act.mutate("undo")}
          >
            Undo
          </Button>
        </div>
      )}
    </article>
  );
}

/** Proposed changes to the entity graph, from graph workflows, to accept or dismiss; and the applied ones to undo. */
export function GraphChangesPage({ run }: { run?: number }) {
  const [status, setStatus] = useState<"proposed" | "applied">("proposed");
  const changes = useGraphChanges(status, run);
  const list = changes.data ?? [];
  return (
    <div className="flex flex-col gap-4 px-4 pb-10 pt-[18px] md:px-6">
      <RoutinesHeader tab="changes" />
      <div className="flex flex-wrap items-center gap-3">
        <Segmented
          label="Which changes"
          value={status}
          onChange={(s) => setStatus(s as "proposed" | "applied")}
          items={[
            { value: "proposed", label: "Proposed" },
            { value: "applied", label: "Applied" },
          ]}
        />
        {run != null && (
          <span className="text-[13px] text-fg-secondary">
            From run #{run} ·{" "}
            <Link href="/routines/changes" className="font-semibold text-fg-accent hover:underline">
              Show all
            </Link>
          </span>
        )}
      </div>
      {changes.isLoading ? (
        <SkeletonRows rows={4} />
      ) : changes.error ? (
        <EmptyState
          tone="error"
          icon={<Waypoints />}
          title="Couldn’t load graph changes"
          actions={<Button onClick={() => changes.refetch()}>Try again</Button>}
        >
          {(changes.error as Error).message}
        </EmptyState>
      ) : list.length ? (
        <div className="flex flex-col gap-3">
          {list.map((ch) => (
            <ChangeCard key={ch.id} ch={ch} />
          ))}
        </div>
      ) : (
        <EmptyState icon={<Waypoints />} title={status === "proposed" ? "Nothing to review" : "No changes made"}>
          {status === "proposed"
            ? "When a workflow that organises the graph isn’t sure of a change (two entities as one, a new type, hiding noise), it proposes it here for you to accept or dismiss."
            : "Merges, links, new types and hides that workflows made show here, so you can undo them."}
        </EmptyState>
      )}
    </div>
  );
}
