"use client";

import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { AudioLines, ChartNoAxesColumn, FileAudio, MessagesSquare } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { AccessRequests, Resources, Sources, Speakers, Tokens } from "@/app/openapi-client";
import { buildAttention, greeting, type AttentionItem } from "@/components/home/attention";
import { QuickImport } from "@/components/home/quick-import";
import { rememberView, useRecentViews, type ViewedKind } from "@/components/home/recently-viewed";
import { useRecordingActions } from "@/components/library/actions";
import { elapsed, isActiveJob, latestJobs, statusView, stepLabel, totalDuration } from "@/components/library/model";
import { Badge } from "@/components/ui/badge";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { count, plural, relative, shortDate, tc } from "@/lib/format";
import { useJobs } from "@/lib/hooks/jobs";
import { needRole, useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

const VIEW_ICON: Record<ViewedKind, typeof FileAudio> = {
  recording: FileAudio,
  speaker: AudioLines,
  chat: MessagesSquare,
  report: ChartNoAxesColumn,
};

function SectionLabel({ children, id }: { children: React.ReactNode; id?: string }) {
  return (
    <h2 id={id} className="text-[13px] font-bold leading-none text-fg-secondary">
      {children}
    </h2>
  );
}

function AttentionRow({ item, onAct, reason }: { item: AttentionItem; onAct: () => void; reason: string | null }) {
  const fail = item.kind === "fail";
  const link = item.action.do.type === "link" ? item.action.do : null;
  return (
    <li
      className={cn(
        "grid grid-cols-[24px_minmax(0,1fr)_auto] items-center gap-3 rounded-md border px-3.5 py-3",
        fail ? "border-red-border bg-red-surface" : "border-gold-border bg-gold-surface",
      )}
    >
      {fail ? (
        <span
          aria-label="Failed"
          role="img"
          className="grid size-[22px] place-items-center rounded-full bg-red text-[11px] font-extrabold text-white"
        >
          ✕
        </span>
      ) : (
        <span aria-label="Needs a decision" role="img" className="grid size-[22px] place-items-center">
          <span className="size-[15px] rotate-45 rounded-[3px] bg-gold" />
        </span>
      )}
      <span className="flex min-w-0 flex-col gap-[3px]">
        <b className="line-clamp-2 text-[14px] font-semibold leading-[1.3] text-fg sm:line-clamp-1">{item.title}</b>
        <span className="line-clamp-2 text-[12.5px] leading-[1.3] text-fg-secondary sm:line-clamp-1">{item.meta}</span>
      </span>
      {link ? (
        <Button asChild variant="secondary" size="sm" onClick={onAct}>
          <Link href={link.href}>{item.action.label}</Link>
        </Button>
      ) : (
        <Button
          variant="secondary"
          size="sm"
          onClick={onAct}
          disabled={Boolean(reason)}
          disabledReason={reason ?? undefined}
        >
          {item.action.label}
        </Button>
      )}
    </li>
  );
}

/** Home (HM1): what needs you, what just arrived, what's processing, and a quick way in. */
export function HomeScreen({ switcher }: { switcher?: React.ReactNode } = {}) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const { me, namespaces, namespace, setNamespace, can, admin } = useArchive();
  const actions = useRecordingActions();
  const views = useRecentViews();
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => setNow(new Date()), []);

  const jobs = useJobs({ limit: 200 });
  const recent = useQuery({
    queryKey: ["recordings", "home", namespace ?? "*"],
    queryFn: () =>
      data(
        Resources.listRecordings({
          client,
          query: { ns: namespace ?? undefined, limit: 50 },
        }),
      ),
    refetchInterval: jobs.data?.running ? 10_000 : 60_000,
  });
  const sources = useQuery({
    queryKey: ["sources"],
    queryFn: () => data(Sources.listSources({ client })),
    enabled: admin,
    staleTime: 30_000,
  });
  const watches = useQuery({
    queryKey: ["watches"],
    queryFn: () => data(Sources.listWatches({ client })),
    enabled: admin,
    staleTime: 30_000,
  });
  const tokens = useQuery({
    queryKey: ["tokens"],
    queryFn: () => data(Tokens.listTokens({ client })),
    staleTime: 60_000,
  });
  const owns = namespaces.some((n) => can("owner", n.name));
  const requests = useQuery({
    queryKey: ["access-requests"],
    queryFn: () => data(AccessRequests.listPendingAccessRequests({ client })),
    enabled: owns,
    staleTime: 30_000,
  });
  const reviewable = namespaces.map((n) => n.name).filter((n) => (!namespace || n === namespace) && can("editor", n));
  const speakerQs = useQueries({
    queries: reviewable.map((ns) => ({
      queryKey: ["speakers", ns],
      queryFn: () => data(Speakers.listSpeakers({ client, query: { ns } })),
      staleTime: 60_000,
    })),
  });

  const scope = namespaces.filter((n) => !namespace || n.name === namespace);
  const nsById = useMemo(() => new Map(namespaces.map((n) => [n.id, n.name])), [namespaces]);
  const latest = useMemo(() => latestJobs(jobs.data?.jobs), [jobs.data]);
  const inScope = (ns: string | null | undefined) => !namespace || ns === namespace;

  // The review queries' data changes identity on every render; key the memo on when they last updated instead.
  const reviewKey = `${reviewable.join(",")}|${speakerQs.map((q) => q.dataUpdatedAt).join(",")}`;
  const items = useMemo(() => {
    const scoped = new Map([...latest].filter(([, j]) => j.space == null || inScope(nsById.get(j.space))));
    const all = buildAttention({
      latestJobs: scoped,
      recent: recent.data ?? [],
      nsById,
      sources: admin ? sources.data : undefined,
      // Only admins can fix a watched folder.
      watches: admin ? (watches.data ?? []).filter((w) => inScope(w.namespace)) : [],
      reviews: reviewable.map((ns, i) => ({
        namespace: ns,
        speakers: speakerQs[i]?.data?.speakers ?? [],
      })),
      requests: (requests.data ?? []).filter((r) => inScope(r.namespace)),
      tokens: tokens.data,
    });
    // Only what this person can act on: failures in namespaces where they can't edit stay out of their way.
    return all.filter((it) => it.namespace === undefined || can("editor", it.namespace));
  }, [
    latest,
    recent.data,
    nsById,
    admin,
    sources.data,
    watches.data,
    requests.data,
    tokens.data,
    namespace,
    reviewKey,
    can,
  ]);

  const act = async (it: AttentionItem) => {
    const a = it.action.do;
    if (a.type === "retry-job") await actions.retryJob(a.job);
    else if (a.type === "reprocess") await actions.reprocess([a.recording]);
    else if (a.type === "test-source") {
      try {
        const h = await data(Sources.testSource({ client, path: { sid: a.source } }));
        toast(
          h.ok
            ? {
                title: "Connected",
                body: "The source can be reached again.",
                tone: "green",
              }
            : {
                title: "Still can’t reach it",
                body: h.error ?? undefined,
                tone: "red",
              },
        );
      } catch (e) {
        toast({
          title: "Couldn’t test the source",
          body: (e as Error).message,
          tone: "red",
        });
      }
      void qc.invalidateQueries({ queryKey: ["sources"] });
    } else if (a.type === "link" && a.namespace) setNamespace(a.namespace);
  };

  const loadingAttention = jobs.isLoading || recent.isLoading;
  const attentionError = jobs.isError || recent.isError;
  const running = (jobs.data?.jobs ?? []).filter(
    (j) => isActiveJob(j) && (j.space == null || inScope(nsById.get(j.space))),
  );
  const totals = {
    recordings: scope.reduce((a, n) => a + ((n.recordings as number) ?? 0), 0),
    ms: scope.reduce((a, n) => a + ((n.ms as number) ?? 0), 0),
    analyzed: scope.reduce((a, n) => a + ((n.analyzed as number) ?? 0), 0),
  };
  const first = (me?.user.name || me?.user.email || "").split(/[\s@]/)[0];

  return (
    <div className="grid content-start gap-6 px-4 py-5 md:px-8 md:py-6 lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
      {switcher && <div className="-mb-3 flex justify-end lg:col-span-2">{switcher}</div>}
      <div className="flex min-w-0 flex-col gap-5">
        <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
          <h1 className="text-[24px] font-bold leading-[1.2] tracking-[-.015em] text-fg">
            {now ? `${greeting(now)}${first ? `, ${first}` : ""}` : "Welcome back"}
          </h1>
          <span className="text-[13px] text-fg-muted" suppressHydrationWarning>
            {now?.toLocaleDateString("en-GB", {
              weekday: "long",
              day: "numeric",
              month: "long",
            })}
          </span>
        </div>

        <section aria-labelledby="attention" className="flex scroll-mt-20 flex-col gap-2">
          <SectionLabel id="attention">
            Needs attention
            {loadingAttention || !items.length ? "" : ` · ${items.length}`}
          </SectionLabel>
          {loadingAttention ? (
            <div className="flex flex-col gap-2" aria-busy="true" aria-label="Loading">
              {[0, 1].map((i) => (
                <Skeleton key={i} className="h-[62px] rounded-md" />
              ))}
            </div>
          ) : attentionError ? (
            <Banner
              tone="error"
              title="Couldn’t check what needs attention."
              action={
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => {
                    void jobs.refetch();
                    void recent.refetch();
                  }}
                >
                  Try again
                </Button>
              }
            >
              {((jobs.error ?? recent.error) as Error | null)?.message}
            </Banner>
          ) : items.length === 0 ? (
            <div
              className="flex items-center gap-3 rounded-md border border-green-border bg-green-surface px-3.5 py-3"
              role="status"
            >
              <span
                aria-hidden
                className="grid size-[22px] place-items-center rounded-full bg-green text-[12px] font-extrabold text-white"
              >
                ✓
              </span>
              <span className="flex flex-col gap-[3px]">
                <b className="text-[14px] font-semibold text-fg">Nothing needs you</b>
                <span className="tabular text-[12.5px] text-fg-secondary">
                  {[
                    plural(totals.recordings, "recording"),
                    totals.ms ? totalDuration(totals.ms) : null,
                    `${count(totals.analyzed)} analyzed`,
                    running.length ? `${count(running.length)} processing` : null,
                    namespace ? `in ${namespace}` : `across ${plural(namespaces.length, "namespace")}`,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
              </span>
            </div>
          ) : (
            <ul className="flex flex-col gap-2">
              {items.map((it) => (
                <AttentionRow
                  key={it.key}
                  item={it}
                  onAct={() => void act(it)}
                  reason={
                    it.namespace !== undefined && !can("editor", it.namespace) ? needRole("editor", it.namespace) : null
                  }
                />
              ))}
            </ul>
          )}
        </section>

        <section aria-labelledby="recent-added" className="flex flex-col gap-2">
          <SectionLabel id="recent-added">Recently added</SectionLabel>
          {recent.isLoading ? (
            <div className="flex flex-col" aria-busy="true" aria-label="Loading">
              {[0, 1, 2, 3].map((i) => (
                <div key={i} className="flex h-11 items-center gap-3 border-b border-border">
                  <Skeleton className="w-1/2" />
                  <span className="flex-1" />
                  <Skeleton className="h-[22px] w-24 rounded-pill" />
                </div>
              ))}
            </div>
          ) : !recent.data?.length ? (
            <p className="text-[13.5px] text-fg-secondary">
              Nothing here yet.{" "}
              {can("editor", namespace) ? (
                <Link href="/import" className="font-semibold text-fg-accent hover:underline">
                  Import transcripts
                </Link>
              ) : (
                "New recordings show up here as they arrive."
              )}
            </p>
          ) : (
            <ul>
              {recent.data.slice(0, 5).map((r) => {
                const v = statusView(r, latest.get(r.id));
                return (
                  <li
                    key={r.id}
                    className="grid h-11 grid-cols-[minmax(0,1fr)_110px] items-center gap-3 border-b border-border sm:grid-cols-[minmax(0,1fr)_110px_90px]"
                  >
                    <span className="flex min-w-0 flex-col gap-[3px]">
                      <Link
                        href={`/resources/${r.id}`}
                        onClick={() =>
                          rememberView({
                            kind: "recording",
                            href: `/resources/${r.id}`,
                            title: r.title || "Untitled",
                          })
                        }
                        className="truncate text-[13.5px] font-semibold leading-tight text-fg hover:underline"
                      >
                        {r.title || "Untitled"}
                      </Link>
                      <span className="truncate text-[12px] leading-none text-fg-muted">
                        {[
                          r.namespace,
                          r.media_kind === "transcript" ? "transcript" : r.media_kind,
                          r.duration_ms ? tc(r.duration_ms) : null,
                        ]
                          .filter(Boolean)
                          .join(" · ")}
                      </span>
                    </span>
                    <span>
                      <Badge tone={v.tone} dot>
                        {v.label}
                      </Badge>
                    </span>
                    <span
                      className="hidden text-right text-[12.5px] text-fg-secondary sm:block"
                      title={shortDate(r.recorded_at, true)}
                    >
                      {relative(r.recorded_at)}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </div>

      <div className="flex min-w-0 flex-col gap-5">
        <QuickImport />

        <section aria-labelledby="processing" className="flex flex-col gap-2.5 rounded-lg border border-border p-4">
          <SectionLabel id="processing">Processing now{running.length ? ` · ${running.length}` : ""}</SectionLabel>
          {jobs.isLoading ? (
            <Skeleton className="w-2/3" />
          ) : !running.length ? (
            <p className="text-[13px] text-fg-muted">
              Nothing running.{" "}
              <Link href="/activity" className="font-semibold text-fg-accent hover:underline">
                Activity
              </Link>{" "}
              has the history.
            </p>
          ) : (
            <ul className="flex flex-col gap-2.5">
              {running.slice(0, 6).map((j) => {
                const pct = Math.round((j.progress ?? 0) * 100);
                return (
                  <li key={j.id} className="flex flex-col gap-[5px]">
                    <span className="flex justify-between gap-3 text-[13px] font-semibold leading-tight">
                      <Link
                        href={j.recording ? `/resources/${j.recording}` : "/activity"}
                        className="truncate text-fg hover:underline"
                      >
                        {(j.title || "Recording").replace(/\s+—.*$/, "")} · {stepLabel(j.next_step)}
                      </Link>
                      <span className="tabular shrink-0 font-medium text-fg-secondary">
                        {j.status === "queued"
                          ? "waiting"
                          : [`${pct}%`, elapsed((j as { started_at?: string }).started_at)].filter(Boolean).join(" · ")}
                      </span>
                    </span>
                    <span
                      className="block h-1 overflow-hidden rounded-pill bg-surface-neutral"
                      role="progressbar"
                      aria-label={`${j.title}: ${stepLabel(j.next_step)}`}
                      aria-valuenow={pct}
                      aria-valuemin={0}
                      aria-valuemax={100}
                    >
                      <span
                        className="block h-full bg-blue transition-[width] duration-slow"
                        style={{ width: `${Math.max(3, pct)}%` }}
                      />
                    </span>
                  </li>
                );
              })}
              {running.length > 6 && (
                <Link href="/activity" className="text-[13px] font-semibold text-fg-accent hover:underline">
                  {count(running.length - 6)} more in Activity
                </Link>
              )}
            </ul>
          )}
        </section>

        <section aria-labelledby="viewed" className="flex flex-col gap-2 rounded-lg border border-border p-4">
          <SectionLabel id="viewed">Recently viewed</SectionLabel>
          {views.length ? (
            <ul className="flex flex-col gap-2">
              {views.slice(0, 5).map((v) => {
                const Icon = VIEW_ICON[v.kind] ?? FileAudio;
                return (
                  <li key={v.href}>
                    <Link
                      href={v.href}
                      className="flex w-full items-center gap-2 text-[13.5px] font-medium leading-[1.3] text-fg hover:underline"
                    >
                      <Icon className="size-[15px] shrink-0 text-fg-muted" aria-hidden />
                      <span className="truncate">{v.title}</span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="text-[13px] text-fg-muted">Recordings you open show up here, on this device.</p>
          )}
        </section>
      </div>
    </div>
  );
}
