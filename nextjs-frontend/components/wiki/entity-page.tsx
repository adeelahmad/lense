"use client";

import { useQuery } from "@tanstack/react-query";
import { History, Link2, Pencil, Shapes, Waypoints } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type ReactNode, useEffect, useState } from "react";

import { Entities } from "@/app/openapi-client";
import { EntityDrawer } from "@/components/entities/entity-drawer";
import { NotePage } from "@/components/notes/note-page";
import { recordingHref } from "@/components/search/links";
import { SimilarSection } from "@/components/similar/similar-list";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { samePath, wikiPath } from "@/components/wiki/links";
import { data, useApiClient } from "@/lib/api/browser";
import { count, shortDate } from "@/lib/format";
import { useArchive } from "@/lib/hooks/session";

type Mention = {
  mention: number;
  recording_id: number;
  title?: string | null;
  t0?: number | null;
  time?: string | null;
  speaker?: string | null;
  text?: string | null;
  highlight?: [number, number] | null;
};
type Connections = {
  entities: { id: number; name: string; type: string; together: number }[];
  speakers: { id: number; name: string; mentions: number }[];
  recordings: { id: number; title?: string | null; mentions: number }[];
};

const MORE = 20;

/** The page an address under /wiki/<ns>/ names, after it is looked up; another way of writing it (any case, an old or
 * merged name) switches the address to the page's own. */
export function WikiPage({ ns, address }: { ns: string; address: string }) {
  const client = useApiClient();
  const router = useRouter();
  const found = useQuery({
    queryKey: ["wiki", ns, address],
    queryFn: () => data(Entities.findWikiPage({ client, path: { ns, address } })),
    retry: false,
    placeholderData: (prev) => prev, // the page stays while a new address of it is looked up
  });
  useEffect(() => {
    if (found.data && !found.data.canonical) router.replace(found.data.path + window.location.hash);
  }, [found.data, router]);

  if (found.isError)
    return (
      <EmptyState
        icon={<Shapes />}
        title="No page by that name"
        actions={
          <Button asChild variant="secondary">
            <Link href={`/entities?ns=${encodeURIComponent(ns)}`}>Entities in {ns}</Link>
          </Button>
        }
      >
        Nothing in {ns} is called that, or you can’t read {ns}.
      </EmptyState>
    );
  if (!found.data) return <Loading />;
  return <EntityPage key={found.data.id} id={found.data.id} />;
}

function Loading() {
  return (
    <div className="mx-auto flex w-full max-w-[1120px] flex-col gap-4 px-6 py-8" aria-busy="true">
      <Skeleton className="h-10 w-1/2" />
      <Skeleton className="h-5 w-2/3" />
      <Skeleton className="h-[240px] w-full" />
    </div>
  );
}

/** One entity's page: what it is, the notes people and the assistant keep on it (the BlockSuite editor), where it's
 * said, the recordings, entities and people it comes up with, and what's like it. */
export function EntityPage({ id }: { id: number }) {
  const client = useApiClient();
  const router = useRouter();
  const toast = useToast();
  const { can } = useArchive();
  const [editing, setEditing] = useState(false);
  const [limit, setLimit] = useState(10);
  const e = useQuery({
    queryKey: ["entity", id],
    queryFn: () => data(Entities.getEntity({ client, path: { eid: id } })),
  });
  const mentions = useQuery({
    queryKey: ["entity-mentions", id, limit],
    queryFn: () => data(Entities.listEntityMentions({ client, path: { eid: id }, query: { limit } })),
    placeholderData: (prev) => prev,
  });
  const links = useQuery({
    queryKey: ["entity", id, "connections"],
    queryFn: () => data(Entities.getEntityConnections({ client, path: { eid: id } })) as Promise<Connections>,
  });

  // Renamed here (or by anyone): the address follows the name.
  const page = e.data?.page;
  useEffect(() => {
    if (page && !samePath(window.location.pathname, page)) router.replace(page + window.location.hash);
  }, [page, router]);

  if (e.isError)
    return (
      <EmptyState icon={<Shapes />} title="This entity isn’t here" tone="error">
        It was merged into another, or it’s in a namespace you can’t read.
      </EmptyState>
    );
  const d = e.data;
  if (!d) return <Loading />;
  const ns = d.namespace ?? "";
  const items = (mentions.data?.items ?? []) as unknown as Mention[];
  const total = mentions.data?.total ?? 0;
  const con = links.data;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(window.location.origin + (d.page ?? ""));
      toast({ title: "Link copied", tone: "green" });
    } catch {
      toast({ title: "Couldn’t copy the link", tone: "red" });
    }
  };

  return (
    <div className="mx-auto flex w-full max-w-[1120px] flex-col gap-6 px-6 py-8">
      <header className="flex flex-col gap-2">
        <nav aria-label="Where this page is" className="flex items-center gap-1.5 text-[12.5px] text-fg-muted">
          <Link href={`/entities?ns=${encodeURIComponent(ns)}`} className="hover:text-fg-accent hover:underline">
            {ns}
          </Link>
          <span aria-hidden>/</span>
          <span>{d.type_label}</span>
        </nav>
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="m-0 text-[30px] font-bold leading-tight tracking-[-.015em] text-fg">{d.name}</h1>
          {d.defined && <Badge tone="intent">On the fixed list</Badge>}
          {d.hidden && <Badge>Hidden</Badge>}
        </div>
        {d.aliases && d.aliases.length > 0 && (
          <p className="m-0 text-[13.5px] text-fg-secondary">Also said as {d.aliases.join(", ")}</p>
        )}
        {d.description ? (
          <p className="m-0 max-w-[760px] text-[15px] leading-[1.5] text-fg">{d.description}</p>
        ) : (
          <p className="m-0 text-[14px] text-fg-muted">No description yet.</p>
        )}
        <div className="flex flex-wrap gap-1.5 pt-1">
          <Button size="xs" variant="ghost" icon={<Pencil />} onClick={() => setEditing(true)}>
            {can("editor", ns) ? "Edit" : "Details"}
          </Button>
          <Button asChild size="xs" variant="ghost" icon={<Waypoints />}>
            <Link href={`/graph?focus=e${id}`}>Graph</Link>
          </Button>
          <Button asChild size="xs" variant="ghost" icon={<History />}>
            <Link href={`/routines/history?entity=${id}`}>History</Link>
          </Button>
          <Button size="xs" variant="ghost" icon={<Link2 />} onClick={() => void copy()}>
            Copy link
          </Button>
        </div>
      </header>

      <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_300px]">
        <main className="flex min-w-0 flex-col gap-8">
          <section aria-label="Notes" className="flex flex-col gap-2">
            <h2 className="m-0 text-[16px] font-bold text-fg-strong">Notes</h2>
            <NotePage key={id} about={`entity:${id}`} embedded />
          </section>

          <section aria-label="Where it’s said" className="flex flex-col gap-3">
            <h2 className="m-0 text-[16px] font-bold text-fg-strong">
              Where it’s said{total ? ` (${count(total)})` : ""}
            </h2>
            {mentions.isLoading && <Skeleton className="h-24 w-full" />}
            {mentions.data && !items.length && (
              <p className="m-0 text-[13.5px] text-fg-secondary">No mentions you can read.</p>
            )}
            <ul className="m-0 flex list-none flex-col gap-3 p-0">
              {items.map((m) => (
                <li key={m.mention} className="flex flex-col gap-[3px]">
                  <Link
                    href={recordingHref(m.recording_id, m.t0 ?? undefined)}
                    className="text-[12.5px] font-semibold text-fg-secondary hover:text-fg-accent hover:underline"
                  >
                    {m.title ?? `Recording ${m.recording_id}`}
                    {m.time ? ` · ${m.time}` : ""}
                    {m.speaker ? ` · ${m.speaker}` : ""}
                  </Link>
                  <Line text={m.text ?? ""} mark={m.highlight} />
                </li>
              ))}
            </ul>
            {items.length < total && (
              <Button
                size="sm"
                variant="secondary"
                className="self-start"
                disabled={mentions.isFetching}
                onClick={() => setLimit((n) => n + MORE)}
              >
                Show more
              </Button>
            )}
          </section>
        </main>

        <aside className="flex flex-col gap-6" aria-label="About this entity">
          <dl className="m-0 grid grid-cols-2 gap-2">
            {[
              ["Mentions", count(d.mentions ?? 0)],
              ["Recordings", count(d.recordings ?? 0)],
              ["First said", d.first ? shortDate(d.first) : "—"],
              ["Last said", d.last ? shortDate(d.last) : "—"],
            ].map(([k, v]) => (
              <div key={k} className="rounded-md border border-border px-3 py-2">
                <dt className="text-[11.5px] font-semibold text-fg-muted">{k}</dt>
                <dd className="tabular m-0 text-[15px] font-bold text-fg">{v}</dd>
              </div>
            ))}
          </dl>
          <Side title="Recordings">
            {con?.recordings.map((r) => (
              <li key={r.id}>
                <Link href={recordingHref(r.id)} className="text-fg-accent hover:underline">
                  {r.title ?? `Recording ${r.id}`}
                </Link>{" "}
                <span className="text-fg-muted">· {count(r.mentions)}</span>
              </li>
            ))}
          </Side>
          <Side title="Comes up with">
            {con?.entities.slice(0, 12).map((x) => (
              <li key={x.id}>
                <Link href={wikiPath(ns, x.name)} className="text-fg-accent hover:underline">
                  {x.name}
                </Link>{" "}
                <span className="text-fg-muted">· {count(x.together)}</span>
              </li>
            ))}
          </Side>
          <Side title="The same in other namespaces">
            {(d.links ?? []).map((x) => (
              <li key={x.id}>
                <Link
                  href={x.namespace && x.name ? wikiPath(x.namespace, x.name) : `/entities/${x.id}`}
                  className="text-fg-accent hover:underline"
                >
                  {x.name ?? `Entity ${x.id}`}
                </Link>{" "}
                <span className="text-fg-muted">· {x.namespace}</span>
              </li>
            ))}
          </Side>
          <Side title="Who says it">
            {con?.speakers.map((s) => (
              <li key={s.id}>
                <Link href={`/speakers/${s.id}`} className="text-fg-accent hover:underline">
                  {s.name}
                </Link>{" "}
                <span className="text-fg-muted">· {count(s.mentions)}</span>
              </li>
            ))}
          </Side>
          <SimilarSection basis={{ entity: id }} />
        </aside>
      </div>
      {editing && <EntityDrawer id={id} ns={ns} onClose={() => setEditing(false)} />}
    </div>
  );
}

/** A list in the side column; nothing when it's empty. */
function Side({ title, children }: { title: string; children?: ReactNode[] }) {
  if (!children?.length) return null;
  return (
    <section aria-label={title} className="flex flex-col gap-1.5">
      <h3 className="m-0 text-[13px] font-bold text-fg-strong">{title}</h3>
      <ul className="m-0 flex list-none flex-col gap-1 p-0 text-[13.5px]">{children}</ul>
    </section>
  );
}

/** A transcript line, with the words that name the entity marked. */
function Line({ text, mark }: { text: string; mark?: [number, number] | null }) {
  const cls = "font-serif text-[14px] leading-[1.45] text-fg";
  if (!mark || mark[0] < 0 || mark[1] > text.length) return <span className={cls}>{text}</span>;
  return (
    <span className={cls}>
      {text.slice(0, mark[0])}
      <mark className="rounded-xs bg-gold-surface px-0.5 text-fg">{text.slice(mark[0], mark[1])}</mark>
      {text.slice(mark[1])}
    </span>
  );
}
