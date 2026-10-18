"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Podcast as PodcastIcon, Rss, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { Feeds } from "@/app/openapi-client";
import type { Feed, FeedItem, FeedSettings } from "@/app/openapi-client/types.gen";
import { ITEM_KINDS, itemHref, itemsText, kindLabel, republish } from "@/components/feeds/model";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox, Field, Input, Select, Textarea } from "@/components/ui/field";
import { Panel } from "@/components/ui/panel";
import { CodeBlock, DateTime, EmptyState, PageHeader, SkeletonRows } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { useArchive } from "@/lib/hooks/session";

const SETTINGS: (keyof FeedSettings)[] = [
  "title",
  "description",
  "author",
  "language",
  "category",
  "explicit",
  "image",
  "max_items",
];

/** A feed's page: its private address (copy it into a reader, or get a new one), its settings, and what it lists, each
 * item with Remove. Deleting the feed can be undone from the toast; it comes back with a new address. */
export function FeedPage({ id }: { id: number }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const router = useRouter();
  const { namespaces, can, admin } = useArchive();
  const q = useQuery({ queryKey: ["feeds", id], queryFn: () => data(Feeds.getFeed({ client, path: { fid: id } })) });
  const items = useQuery({
    queryKey: ["feeds", id, "items"],
    queryFn: () => data(Feeds.listFeedItems({ client, path: { fid: id }, query: { limit: 200 } })),
    enabled: q.isSuccess,
  });
  const feed = q.data;
  const ns = feed?.namespace ?? namespaces.find((n) => n.id === feed?.space)?.name;
  const editor = can("editor", ns);
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["feeds"] });
  };

  const rotate = useMutation({
    mutationFn: () => data(Feeds.rotateFeedToken({ client, path: { fid: id } })),
    onSuccess: (f) => {
      qc.setQueryData(["feeds", id], f);
      void qc.invalidateQueries({ queryKey: ["feeds", "list"] });
      toast({ title: "New address", body: "The old one stopped working. Copy this one into your app.", tone: "green" });
    },
    onError: (e: Error) => toast({ title: "Couldn’t make a new address", body: e.message, tone: "red" }),
  });

  const put = useMutation({
    mutationFn: (it: FeedItem) =>
      data(Feeds.publishToFeed({ client, path: { fid: id }, body: republish(it) })).then((r) => ({ it, r })),
    onSuccess: ({ r }) => {
      refresh();
      if (r.refused.length) toast({ title: "Couldn’t put it back", body: r.refused[0].why, tone: "red" });
    },
    onError: (e: Error) => toast({ title: "Couldn’t put it back", body: e.message, tone: "red" }),
  });
  // Taking an item out is done at once with an Undo, which adds the same thing again under the same guid.
  const remove = useMutation({
    mutationFn: (it: FeedItem) => data(Feeds.removeFeedItem({ client, path: { fid: id, iid: it.id } })).then(() => it),
    onSuccess: (it) => {
      refresh();
      toast({
        title: `Took “${it.title || ITEM_KINDS[it.kind] || "the item"}” out of the feed`,
        body: "Readers stop seeing it when they next check.",
        tone: "green",
        action: { label: "Undo", onClick: () => put.mutate(it) },
      });
    },
    onError: (e: Error) => toast({ title: "Couldn’t take it out", body: e.message, tone: "red" }),
  });

  // Deleting is undone by making the feed again with its settings and items; its address is new, since the old token
  // is gone for good.
  const restore = useMutation({
    mutationFn: async ({ f, list }: { f: Feed; list: FeedItem[] }) => {
      const made = await data(
        Feeds.createFeed({
          client,
          body: {
            namespace: f.namespace ?? ns ?? "",
            kind: f.kind,
            title: f.title,
            description: f.description,
            author: f.author,
            language: f.language,
            category: f.category,
            explicit: f.explicit,
            image: f.image,
            max_items: f.max_items,
          },
        }),
      );
      const body = {
        recordings: list.flatMap((it) => republish(it).recordings ?? []),
        activities: list.flatMap((it) => republish(it).activities ?? []),
        texts: list.flatMap((it) => republish(it).texts ?? []),
      };
      if (body.recordings.length || body.activities.length || body.texts.length)
        await data(Feeds.publishToFeed({ client, path: { fid: made.id }, body }));
      return made;
    },
    onSuccess: (made) => {
      refresh();
      toast({ title: "Feed is back", body: "With a new address: copy it into your app again.", tone: "green" });
      router.replace(`/feeds/${made.id}`);
    },
    onError: (e: Error) => toast({ title: "Couldn’t bring the feed back", body: e.message, tone: "red" }),
  });
  const del = useMutation({
    mutationFn: () =>
      data(Feeds.deleteFeed({ client, path: { fid: id } })).then(() => ({ f: feed!, list: items.data ?? [] })),
    onSuccess: (kept) => {
      refresh();
      toast({
        title: `Deleted “${kept.f.title}”`,
        body: "Its address stopped working. What it listed is still in Lens.",
        tone: "green",
        action: { label: "Undo", onClick: () => restore.mutate(kept) },
      });
      router.replace("/feeds");
    },
    onError: (e: Error) => toast({ title: "Couldn’t delete it", body: e.message, tone: "red" }),
  });

  if (q.isPending) return <SkeletonRows rows={6} className="px-4 py-5 md:px-6" />;
  if (q.isError || !feed) {
    const missing = q.error instanceof ApiError && (q.error.status === 404 || q.error.status === 403);
    return (
      <EmptyState
        tone="error"
        title={missing ? "No such feed" : "Couldn’t load this feed"}
        actions={
          <Button asChild variant="secondary">
            <Link href="/feeds">All feeds</Link>
          </Button>
        }
      >
        {missing ? "It was deleted, or it’s in a namespace you can’t read." : (q.error as Error).message}
      </EmptyState>
    );
  }

  return (
    <div className="flex flex-col gap-5 px-4 py-5 md:px-6">
      <PageHeader
        className="mb-0"
        title={
          <span className="inline-flex items-center gap-2">
            {feed.kind === "podcast" ? (
              <PodcastIcon aria-hidden className="size-5 text-fg-secondary" />
            ) : (
              <Rss aria-hidden className="size-5 text-fg-secondary" />
            )}
            {feed.title}
          </span>
        }
        meta={
          <span className="inline-flex flex-wrap items-center gap-2">
            <Badge tone={feed.kind === "podcast" ? "intent" : "neutral"}>{kindLabel(feed.kind)} feed</Badge>
            {ns && <span>{ns}</span>}
            <span>{itemsText(feed.items)}</span>
          </span>
        }
        actions={
          editor && (
            <Button
              variant="danger-ghost"
              icon={<Trash2 />}
              disabled={del.isPending}
              onClick={() => del.mutate()}
              disabledReason={del.isPending ? "Deleting…" : undefined}
            >
              Delete
            </Button>
          )
        }
      />

      <Panel
        title="Address"
        subtitle="Add it to your feed reader or podcast app. It’s private: the address is all a reader needs, so share it like a password."
        actions={
          editor && (
            <Button size="sm" variant="secondary" disabled={rotate.isPending} onClick={() => rotate.mutate()}>
              New address
            </Button>
          )
        }
        bodyClassName="flex flex-col gap-2 px-4 pb-4"
      >
        <CodeBlock inline text={feed.url} label="the address" className="max-w-full" />
        <p className="text-[12.5px] text-fg-muted">
          {feed.fetched_at ? (
            <>
              An app last read it <DateTime iso={feed.fetched_at} />.
            </>
          ) : (
            "No app has read it yet."
          )}{" "}
          {feed.kind === "podcast"
            ? "In a podcast app, choose to follow a show by its address (in Apple Podcasts: Library, then Follow a Show by URL)."
            : "Feed readers take the address as a new subscription."}{" "}
          A new address stops the old one at once.
        </p>
      </Panel>

      {editor && <SettingsPanel feed={feed} onSaved={(f) => qc.setQueryData(["feeds", id], f)} />}

      <Panel
        title="Items"
        subtitle={
          feed.kind === "podcast"
            ? "Newest first. A podcast app sees the ones with audio; an episode shows once it’s made."
            : "Newest first, as readers see them."
        }
      >
        {items.isPending ? (
          <SkeletonRows rows={3} />
        ) : items.isError ? (
          <EmptyState tone="error" title="Couldn’t load its items">
            {(items.error as Error).message}
          </EmptyState>
        ) : !items.data.length ? (
          <EmptyState
            title="Nothing in it yet"
            actions={
              admin && (
                <Button asChild variant="secondary">
                  <Link href="/routines">Routines</Link>
                </Button>
              )
            }
          >
            A routine fills it on a schedule with the Publish to feed node, or makes a Podcast episode about what’s new.
            Agents add to it through the API and MCP.
          </EmptyState>
        ) : (
          <ul className="divide-y divide-border">
            {items.data.map((it) => {
              const href = itemHref(it);
              const title = it.title || ITEM_KINDS[it.kind] || "Untitled";
              return (
                <li key={it.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5">
                  <Badge>{ITEM_KINDS[it.kind] ?? it.kind}</Badge>
                  <span className="min-w-0 flex-1 truncate text-[14px] text-fg">
                    {href ? (
                      href.startsWith("/") ? (
                        <Link href={href} className="font-semibold hover:underline">
                          {title}
                        </Link>
                      ) : (
                        <a
                          href={href}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1 font-semibold hover:underline"
                        >
                          {title}
                          <ExternalLink aria-hidden className="size-3" />
                        </a>
                      )
                    ) : (
                      <span className="font-semibold">{title}</span>
                    )}
                    {it.summary && <span className="ml-2 text-[13px] text-fg-secondary">{it.summary}</span>}
                  </span>
                  {it.by?.startsWith("routine:") && <span className="text-[12px] text-fg-muted">by a routine</span>}
                  <DateTime iso={it.published_at} className="text-[13px] text-fg-muted" />
                  {editor && (
                    <Button
                      size="xs"
                      variant="ghost"
                      aria-label={`Remove ${title}`}
                      disabled={remove.isPending}
                      onClick={() => remove.mutate(it)}
                    >
                      Remove
                    </Button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </Panel>
    </div>
  );
}

/** The feed's settings as readers see them, saved together (Enter in a field saves). */
function SettingsPanel({ feed, onSaved }: { feed: Feed; onSaved: (f: Feed) => void }) {
  const client = useApiClient();
  const toast = useToast();
  const from = (f: Feed): FeedSettings => ({
    title: f.title,
    description: f.description ?? "",
    author: f.author ?? "",
    language: f.language ?? "",
    category: f.category ?? "",
    explicit: Boolean(f.explicit),
    image: f.image ?? "",
    max_items: f.max_items,
  });
  const [v, setV] = useState<FeedSettings>(() => from(feed));
  useEffect(() => setV(from(feed)), [feed]);
  const dirty = SETTINGS.some((k) => (v[k] ?? "") !== (from(feed)[k] ?? ""));
  const save = useMutation({
    mutationFn: () =>
      data(
        Feeds.updateFeed({
          client,
          path: { fid: feed.id },
          body: {
            title: String(v.title ?? "").trim() || feed.title,
            description: String(v.description ?? "").trim() || null,
            author: String(v.author ?? "").trim() || null,
            language: String(v.language ?? "").trim() || null,
            category: String(v.category ?? "").trim() || null,
            explicit: Boolean(v.explicit),
            image: String(v.image ?? "").trim() || null,
            max_items: v.max_items ?? feed.max_items,
          },
        }),
      ),
    onSuccess: (f) => {
      onSaved(f);
      toast({ title: "Saved", body: "Readers see the change when they next check.", tone: "green" });
    },
    onError: (e: Error) => toast({ title: "Couldn’t save", body: e.message, tone: "red" }),
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (dirty && !save.isPending) save.mutate();
  };
  const set = (patch: Partial<FeedSettings>) => setV((x) => ({ ...x, ...patch }));
  const podcast = feed.kind === "podcast";
  return (
    <Panel
      as="div"
      title="Settings"
      subtitle={podcast ? "What podcast apps show for the show." : "What readers show for the feed."}
      bodyClassName="px-4 pb-4"
    >
      <form onSubmit={submit} className="flex flex-col gap-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Title">
            {(f) => (
              <Input
                id={f.id}
                value={String(v.title ?? "")}
                maxLength={200}
                onChange={(e) => set({ title: e.target.value })}
              />
            )}
          </Field>
          <Field label="Author" optional hint={podcast ? "Shown as the show’s author" : undefined}>
            {(f) => (
              <Input
                id={f.id}
                aria-describedby={f.describedBy}
                value={String(v.author ?? "")}
                maxLength={200}
                onChange={(e) => set({ author: e.target.value })}
              />
            )}
          </Field>
        </div>
        <Field label="Description" optional>
          {(f) => (
            <Textarea
              id={f.id}
              rows={2}
              value={String(v.description ?? "")}
              maxLength={4000}
              onChange={(e) => set({ description: e.target.value })}
            />
          )}
        </Field>
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Language" optional hint="e.g. en or en-GB">
            {(f) => (
              <Input
                id={f.id}
                aria-describedby={f.describedBy}
                value={String(v.language ?? "")}
                maxLength={16}
                onChange={(e) => set({ language: e.target.value })}
              />
            )}
          </Field>
          <Field label="Category" optional hint={podcast ? "An Apple Podcasts category, e.g. Technology" : undefined}>
            {(f) => (
              <Input
                id={f.id}
                aria-describedby={f.describedBy}
                value={String(v.category ?? "")}
                maxLength={100}
                onChange={(e) => set({ category: e.target.value })}
              />
            )}
          </Field>
          <Field label="Items shown" hint="The newest 1 to 500 a reader sees">
            {(f) => (
              <Select
                id={f.id}
                aria-describedby={f.describedBy}
                value={String(v.max_items ?? feed.max_items)}
                onChange={(e) => set({ max_items: Number(e.target.value) })}
                options={[20, 50, 100, 200, 500].map((n) => ({ value: String(n), label: String(n) }))}
              />
            )}
          </Field>
        </div>
        <Field label="Artwork" optional hint="An https:// address of a square image; podcast apps show it">
          {(f) => (
            <Input
              id={f.id}
              aria-describedby={f.describedBy}
              mono
              value={String(v.image ?? "")}
              maxLength={2000}
              placeholder="https://"
              onChange={(e) => set({ image: e.target.value })}
            />
          )}
        </Field>
        {podcast && (
          <Checkbox
            checked={Boolean(v.explicit)}
            onCheckedChange={(c) => set({ explicit: c === true })}
            label="Explicit content"
          />
        )}
        <div className="flex items-center gap-3">
          <Button type="submit" variant="primary" size="sm" disabled={!dirty || save.isPending}>
            {save.isPending ? "Saving…" : "Save"}
          </Button>
          {dirty && (
            <Button type="button" variant="ghost" size="sm" onClick={() => setV(from(feed))}>
              Reset
            </Button>
          )}
        </div>
      </form>
    </Panel>
  );
}
