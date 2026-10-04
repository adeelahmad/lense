"use client";

import { Check, EyeOff, Inbox, Plus, RadioTower, Settings } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import type { Sensor } from "@/app/openapi-client/types.gen";
import { useSensorActions, useSensors } from "@/components/sensors/data";
import { AddSensorDialog, SensorsTabs, SensorTile, StatusBadge } from "@/components/sensors/parts";
import { groups, handlingText, hubText } from "@/components/sensors/sensor-model";
import { SourcesPage } from "@/components/sources/sources-page";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Panel } from "@/components/ui/panel";
import { DateTime, EmptyState, SkeletonRows } from "@/components/ui/states";
import { Table, Td, Th, THead, Tr } from "@/components/ui/table";
import { plural } from "@/lib/format";
import { useArchive } from "@/lib/hooks/session";

/** The heading every Sensors tab shares. */
export function SensorsHeader({
  tab,
  newCount,
  actions,
}: {
  tab: "sensors" | "sources" | "logins";
  newCount?: number;
  actions?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2.5">
        <h1 className="flex-1 text-[24px] font-bold leading-tight tracking-[-.015em] text-fg">Sensors</h1>
        {actions}
      </div>
      <SensorsTabs tab={tab} newCount={newCount} />
    </div>
  );
}

function SensorName({ s }: { s: Sensor }) {
  return (
    <div className="flex min-w-0 items-center gap-3">
      <SensorTile type={s.type} />
      <div className="flex min-w-0 flex-col gap-0.5">
        <Link href={`/sensors/${s.id}`} className="truncate font-semibold text-fg hover:text-fg-accent hover:underline">
          {s.name}
        </Link>
        <span className="truncate text-[12px] text-fg-muted">
          {s.label}
          {s.device && s.device !== s.name ? ` · ${s.device}` : ""}
        </span>
      </div>
    </div>
  );
}

/** New sensors the hub found: each with the handling suggested for it, to apply, ignore or look at. */
function InboxPanel({ inbox }: { inbox: Sensor[] }) {
  const { update, suggestion, review } = useSensorActions();
  return (
    <Panel
      tone="intent"
      title={`${plural(inbox.length, "new sensor")} to look at`}
      subtitle="Everything they send is kept with the default handling until you decide. Suggestions come from what each one has sent so far."
      actions={
        inbox.length > 1 && (
          <Button
            size="sm"
            variant="primary"
            icon={<Check />}
            disabled={review.isPending}
            onClick={() => review.mutate()}
          >
            Apply all suggestions
          </Button>
        )
      }
    >
      <ul className="flex flex-col divide-y divide-border">
        {inbox.map((s) => (
          <li key={s.id} className="flex flex-wrap items-center gap-3 py-3 first:pt-1 last:pb-0">
            <div className="min-w-[220px] flex-1">
              <SensorName s={s} />
            </div>
            <p className="min-w-[240px] flex-[2] text-[13px] text-fg-secondary">
              {s.suggested?.reason ?? "Nothing to suggest yet."}{" "}
              <span className="text-fg-muted">
                {plural(s.channels ?? 0, "stream")} · {plural(s.readings ?? 0, "reading")}
              </span>
            </p>
            <div className="flex items-center gap-1.5">
              <Button
                size="sm"
                variant="secondary"
                icon={<Check />}
                disabled={suggestion.isPending && suggestion.variables?.id === s.id}
                onClick={() => suggestion.mutate({ id: s.id, name: s.name })}
              >
                Apply
              </Button>
              <Button
                size="sm"
                variant="ghost"
                icon={<EyeOff />}
                disabled={update.isPending && update.variables?.id === s.id}
                onClick={() => update.mutate({ id: s.id, body: { status: "ignored" }, done: `Ignoring ${s.name}` })}
              >
                Ignore
              </Button>
            </div>
          </li>
        ))}
      </ul>
    </Panel>
  );
}

function StreamTable({ list, label }: { list: Sensor[]; label: string }) {
  return (
    <div className="overflow-hidden rounded-md border border-border">
      <Table aria-label={label}>
        <THead className="border-t-0">
          <tr>
            <Th>Sensor</Th>
            <Th>Namespace</Th>
            <Th>Keeps</Th>
            <Th>Streams</Th>
            <Th>Last heard</Th>
            <Th>Status</Th>
          </tr>
        </THead>
        <tbody>
          {list.map((s) => (
            <Tr key={s.id} className="h-[58px]">
              <Td className="max-w-[320px]">
                <SensorName s={s} />
              </Td>
              <Td className="text-[13px] text-fg-secondary">{s.namespace ?? "—"}</Td>
              <Td className="max-w-[320px] text-[12.5px] text-fg-secondary">
                <span className="line-clamp-2">{handlingText(s.handling)}</span>
              </Td>
              <Td className="whitespace-nowrap text-[13px] text-fg-secondary">
                {s.channels ?? 0} · {plural(s.readings ?? 0, "reading")}
              </Td>
              <Td className="whitespace-nowrap text-[13px] text-fg-secondary">
                <DateTime iso={s.last_seen_at} />
              </Td>
              <Td>
                <StatusBadge status={s.status} />
              </Td>
            </Tr>
          ))}
        </tbody>
      </Table>
    </div>
  );
}

/**
 * Sensors (admins): everything that feeds Lens. The hub's state, new sensors to decide about, the streams being
 * kept, and the file sensors (Sources). People who aren't admins see the watched folders, as before.
 */
export function SensorsPage() {
  const { admin, me } = useArchive();
  const sensors = useSensors();
  const [adding, setAdding] = useState(false);
  const [showIgnored, setShowIgnored] = useState(false);

  if (me && !admin) return <SourcesPage />;

  const all = sensors.data?.sensors ?? [];
  const g = groups(all);
  const hub = sensors.data?.hub;
  const h = hub ? hubText(hub) : null;

  return (
    <div className="flex flex-col gap-4 px-4 pb-10 pt-[18px] md:px-6">
      <SensorsHeader
        tab="sensors"
        newCount={g.inbox.length}
        actions={
          <>
            <Button asChild size="sm" variant="ghost">
              <Link href="/settings/sensors">
                <Settings /> Hub settings
              </Link>
            </Button>
            <Button
              size="sm"
              variant="primary"
              icon={<Plus />}
              disabled={!sensors.data}
              disabledReason="Loading…"
              onClick={() => setAdding(true)}
            >
              Add sensor
            </Button>
          </>
        }
      />
      {sensors.isLoading || !me ? (
        <SkeletonRows rows={5} />
      ) : sensors.error ? (
        <EmptyState
          tone="error"
          icon={<RadioTower />}
          title="Couldn’t load sensors"
          actions={<Button onClick={() => sensors.refetch()}>Try again</Button>}
        >
          {(sensors.error as Error).message}
        </EmptyState>
      ) : (
        <>
          {h && (
            <Banner
              tone={h.tone}
              title={h.title}
              action={
                !hub?.enabled && (
                  <Button asChild size="xs" variant="secondary">
                    <Link href="/settings/sensors">Turn on</Link>
                  </Button>
                )
              }
            >
              {h.body}
            </Banner>
          )}
          {g.inbox.length > 0 && <InboxPanel inbox={g.inbox} />}
          {g.streams.length ? (
            <StreamTable list={g.streams} label="Stream sensors" />
          ) : (
            !g.inbox.length && (
              <EmptyState
                icon={<Inbox />}
                title="No devices yet"
                actions={
                  <Button variant="primary" icon={<Plus />} onClick={() => setAdding(true)}>
                    Add sensor
                  </Button>
                }
              >
                Point a router, DNS server or anything that speaks MQTT or syslog at Lens, and it shows up here by
                itself. Anything that can send HTTP can use a webhook.
              </EmptyState>
            )
          )}
          <section className="flex flex-col gap-2">
            <div className="flex items-center gap-3">
              <h2 className="flex-1 text-[16px] font-bold text-fg">Files, email and calendars</h2>
              <Button asChild size="sm" variant="secondary">
                <Link href="/sources">Manage</Link>
              </Button>
            </div>
            {g.files.length ? (
              <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {g.files.map((s) => (
                  <li key={s.id} className="flex items-center gap-3 rounded-md border border-border px-3 py-2.5">
                    <SensorTile type={s.type} />
                    <div className="flex min-w-0 flex-col">
                      <span className="truncate text-[14px] font-semibold text-fg">{s.name}</span>
                      <span className="text-[12px] text-fg-muted">
                        {s.label} · {plural(s.channels ?? 0, "watched folder")}
                      </span>
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-[13px] text-fg-muted">
                No storage, email or calendar connections yet. <Link href="/sources">Add one in Sources</Link>.
              </p>
            )}
          </section>
          {g.ignored.length > 0 && (
            <section className="flex flex-col gap-2">
              <button
                type="button"
                className="self-start text-[13px] font-semibold text-fg-accent hover:underline"
                onClick={() => setShowIgnored((v) => !v)}
                aria-expanded={showIgnored}
              >
                {showIgnored ? "Hide" : "Show"} {plural(g.ignored.length, "ignored sensor")}
              </button>
              {showIgnored && <StreamTable list={g.ignored} label="Ignored sensors" />}
            </section>
          )}
        </>
      )}
      {adding && sensors.data && (
        <AddSensorDialog open onOpenChange={setAdding} types={sensors.data.types} hub={sensors.data.hub} />
      )}
    </div>
  );
}
