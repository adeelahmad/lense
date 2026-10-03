"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Check, Ellipsis, KeyRound, RadioTower } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { Sensors } from "@/app/openapi-client";
import type { Pattern, SensorDetail as Detail, Stream } from "@/app/openapi-client/types.gen";
import {
  usePatterns,
  useReadings,
  useSensor,
  useSensorActions,
  useSensors,
  useSeries,
} from "@/components/sensors/data";
import {
  BridgeFields,
  HandlingEditor,
  NamespaceSelect,
  SensorTile,
  Sparkline,
  StatusBadge,
  TokenReveal,
} from "@/components/sensors/parts";
import {
  connectText,
  fillHours,
  formatNumber,
  LABELS,
  lastText,
  SEVERITIES,
  sparkPath,
  STATUS,
  type Handling,
  type Status,
  type StreamType,
} from "@/components/sensors/sensor-model";
import { Badge } from "@/components/ui/badge";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select, Switch } from "@/components/ui/field";
import { Menu, MenuContent, MenuItem, MenuSeparator, MenuTrigger } from "@/components/ui/menu";
import { Panel } from "@/components/ui/panel";
import { DateTime, EmptyState, SkeletonRows } from "@/components/ui/states";
import { Segmented, Tabs } from "@/components/ui/tabs";
import { Table, Td, Th, THead, Tr } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { count, plural } from "@/lib/format";
import { useArchive } from "@/lib/hooks/session";

const RANGES = [
  { value: "24", label: "Day" },
  { value: "168", label: "Week" },
  { value: "720", label: "Month" },
];

function StreamCard({ sid, st, hours }: { sid: number; st: Stream; hours: number }) {
  const fields = st.fields ?? [];
  const [picked, setField] = useState<string | null>(null);
  // Fields can arrive after the card first shows: chart the first one until another is picked.
  const field = st.kind === "json" ? (picked ?? fields[0] ?? null) : null;
  const charted = st.kind === "number" || st.kind === "boolean" || (st.kind === "json" && field != null);
  const series = useSeries(sid, st.id, field, hours, charted);
  const path = useMemo(() => {
    if (!series.data) return "";
    return sparkPath(
      fillHours(series.data, hours).map((p) => p?.avg ?? null),
      220,
      40,
    );
  }, [series.data, hours]);
  const values = (series.data ?? []).filter((p) => p.min != null);
  const lo = values.length ? Math.min(...values.map((p) => p.min as number)) : null;
  const hi = values.length ? Math.max(...values.map((p) => p.max as number)) : null;
  return (
    <li className="flex flex-col gap-2 rounded-md border border-border p-3">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <div className="truncate font-mono text-[13px] font-semibold text-fg" title={st.name}>
            {st.name}
          </div>
          <div className="text-[12px] text-fg-muted">
            {st.kind ?? "?"} · {plural(st.count ?? 0, "reading")}
            {st.dropped ? ` · ${count(st.dropped)} dropped` : ""}
          </div>
        </div>
        <div className="max-w-[45%] truncate text-right text-[18px] font-bold tabular text-fg" title={lastText(st)}>
          {lastText(st)}
        </div>
      </div>
      {st.kind === "json" && fields.length > 1 && (
        <Select
          size="sm"
          aria-label={`Number to chart for ${st.name}`}
          value={field ?? ""}
          onChange={(e) => setField(e.target.value)}
          options={fields}
        />
      )}
      {charted ? (
        <div className="flex items-end gap-3">
          <Sparkline path={path} w={220} h={40} className="min-w-0 flex-1" />
          {lo != null && hi != null && (
            <span className="whitespace-nowrap text-[11.5px] text-fg-muted">
              {formatNumber(lo)} – {formatNumber(hi)}
            </span>
          )}
        </div>
      ) : (
        st.last_text && <p className="line-clamp-2 font-mono text-[12px] text-fg-secondary">{st.last_text}</p>
      )}
      <div className="text-[11.5px] text-fg-muted">
        Last <DateTime iso={st.last_at} />
      </div>
    </li>
  );
}

function ReadingsTable({ sid, streams }: { sid: number; streams: Stream[] }) {
  const [stream, setStream] = useState<string>("");
  const readings = useReadings(sid, stream || null, 100);
  const rows = readings.data ?? [];
  return (
    <section className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="flex-1 text-[16px] font-bold text-fg">Latest readings</h2>
        {streams.length > 1 && (
          <Select
            size="sm"
            className="w-[240px]"
            aria-label="Stream"
            value={stream}
            onChange={(e) => setStream(e.target.value)}
            options={[{ value: "", label: "Every stream" }, ...streams.map((s) => ({ value: s.id, label: s.name }))]}
          />
        )}
      </div>
      {readings.isLoading ? (
        <SkeletonRows rows={4} />
      ) : !rows.length ? (
        <p className="text-[13px] text-fg-muted">Nothing kept yet.</p>
      ) : (
        <div className="overflow-hidden rounded-md border border-border">
          <Table aria-label="Readings">
            <THead className="border-t-0">
              <tr>
                <Th>When</Th>
                <Th>Stream</Th>
                <Th>Reading</Th>
              </tr>
            </THead>
            <tbody>
              {rows.map((r) => (
                <Tr key={r.id}>
                  <Td className="whitespace-nowrap text-[12.5px] text-fg-secondary">
                    <DateTime iso={r.at} />
                  </Td>
                  <Td className="max-w-[220px] truncate font-mono text-[12.5px]">{r.stream_name ?? r.stream}</Td>
                  <Td className="text-[13px]">
                    {r.level != null && r.level <= 4 && (
                      <Badge tone={r.level <= 3 ? "red" : "gate"} className="mr-2">
                        {SEVERITIES[r.level]}
                      </Badge>
                    )}
                    {r.value != null ? (
                      <span className="tabular font-semibold">{formatNumber(r.value)}</span>
                    ) : (
                      <span className="break-all font-mono text-[12.5px] text-fg-strong">{r.text}</span>
                    )}
                    {r.fields && r.value == null && !r.text && (
                      <span className="font-mono text-[12px] text-fg-secondary">
                        {Object.entries(r.fields)
                          .map(([k, v]) => `${k} ${formatNumber(v)}`)
                          .join(" · ")}
                      </span>
                    )}
                  </Td>
                </Tr>
              ))}
            </tbody>
          </Table>
        </div>
      )}
    </section>
  );
}

function PatternsTab({ sid }: { sid: number }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const [label, setLabel] = useState<string>("all");
  const patterns = usePatterns(sid, label === "all" ? null : label);
  const update = useMutation({
    mutationFn: (v: { p: Pattern; label?: string | null; action?: "keep" | "drop" }) =>
      data(
        Sensors.updatePattern({
          client,
          path: { pid: v.p.id },
          body: {
            ...(v.label !== undefined ? { label: v.label as Pattern["label"] } : {}),
            ...(v.action ? { action: v.action } : {}),
          },
        }),
      ),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["sensor-patterns", sid] }),
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t change the pattern", body: e.message }),
  });
  const rows = patterns.data ?? [];
  return (
    <div className="flex flex-col gap-3">
      <p className="text-[13px] text-fg-secondary">
        Log lines grouped by what’s left when the parts that change (addresses, names, numbers) are taken out. Label
        them yourself, or turn on triage in Settings to let the decision model do it. Dropped kinds are still counted.
      </p>
      <Segmented
        label="Show"
        value={label}
        onChange={setLabel}
        items={[
          { value: "all", label: "All" },
          { value: "none", label: "Unlabelled" },
          { value: "alert", label: "Alerts" },
          { value: "notable", label: "Notable" },
          { value: "routine", label: "Routine" },
        ]}
      />
      {patterns.isLoading ? (
        <SkeletonRows rows={4} />
      ) : !rows.length ? (
        <p className="text-[13px] text-fg-muted">No log lines of this kind.</p>
      ) : (
        <div className="overflow-hidden rounded-md border border-border">
          <Table aria-label="Kinds of log line">
            <THead className="border-t-0">
              <tr>
                <Th>Kind of line</Th>
                <Th>Seen</Th>
                <Th>Label</Th>
                <Th>Keep</Th>
              </tr>
            </THead>
            <tbody>
              {rows.map((p) => (
                <Tr key={p.id}>
                  <Td className="max-w-[560px]">
                    <div className="break-words font-mono text-[12.5px] text-fg">{p.template}</div>
                    {p.example && p.example !== p.template && (
                      <div
                        className="mt-0.5 line-clamp-1 break-all font-mono text-[11.5px] text-fg-muted"
                        title={p.example}
                      >
                        {p.example}
                      </div>
                    )}
                    {p.stream_name && <div className="text-[11.5px] text-fg-muted">{p.stream_name}</div>}
                  </Td>
                  <Td className="whitespace-nowrap text-[12.5px] text-fg-secondary">
                    {count(p.count ?? 0)}×
                    <div className="text-[11.5px] text-fg-muted">
                      last <DateTime iso={p.last_at} />
                    </div>
                  </Td>
                  <Td className="whitespace-nowrap">
                    <div className="flex flex-col gap-1">
                      <Select
                        size="sm"
                        className="w-[140px]"
                        aria-label="Label"
                        value={p.label ?? ""}
                        disabled={update.isPending && update.variables?.p.id === p.id}
                        onChange={(e) => update.mutate({ p, label: e.target.value || null })}
                        options={[
                          { value: "", label: "No label" },
                          ...Object.entries(LABELS).map(([value, l]) => ({ value, label: l.label })),
                        ]}
                      />
                      {p.label && p.label_by && (
                        <span className="text-[11px] text-fg-muted">
                          by {p.label_by}
                          {p.sure === false ? ", not sure: check it" : ""}
                        </span>
                      )}
                    </div>
                  </Td>
                  <Td>
                    <Switch
                      aria-label={`Keep lines like ${p.template}`}
                      checked={(p.action ?? "keep") === "keep"}
                      disabled={update.isPending && update.variables?.p.id === p.id}
                      onCheckedChange={(v) => update.mutate({ p, action: v ? "keep" : "drop" })}
                    />
                  </Td>
                </Tr>
              ))}
            </tbody>
          </Table>
        </div>
      )}
    </div>
  );
}

function NameField({ s }: { s: Detail }) {
  const { update } = useSensorActions();
  const [name, setName] = useState(s.name);
  useEffect(() => setName(s.name), [s.name]);
  return (
    <Field label="Name">
      {({ id }) => (
        <div className="flex gap-2">
          <Input id={id} value={name} onChange={(e) => setName(e.target.value)} />
          <Button
            size="sm"
            className="h-10"
            disabled={!name.trim() || name === s.name || update.isPending}
            onClick={() => update.mutate({ id: s.id, body: { name: name.trim() }, done: "Renamed" })}
          >
            Rename
          </Button>
        </div>
      )}
    </Field>
  );
}

function BridgeSettings({ s }: { s: Detail }) {
  const { update } = useSensorActions();
  const saved = useMemo(() => {
    const p: Record<string, string> = {};
    for (const [k, v] of Object.entries(s.params ?? {})) p[k] = String(v ?? "");
    return p;
  }, [s.params]);
  const [params, setParams] = useState(saved);
  const [password, setPassword] = useState<string | undefined>();
  useEffect(() => setParams(saved), [saved]);
  const dirty = JSON.stringify(params) !== JSON.stringify(saved) || password !== undefined;
  return (
    <Panel
      title="Connection"
      subtitle="Lens subscribes to these topics; each device under them becomes a sensor of its own."
    >
      <div className="flex flex-col gap-4">
        <BridgeFields
          params={params}
          onChange={setParams}
          password={password}
          onPassword={setPassword}
          passwordSet={Boolean(s.secrets?.pass?.set)}
        />
        {s.health && (s.health as { error?: string }).error && (
          <Banner tone="error">{String((s.health as { error?: string }).error)}</Banner>
        )}
        <div>
          <Button
            size="sm"
            variant="primary"
            disabled={!dirty || update.isPending}
            onClick={() =>
              update.mutate(
                {
                  id: s.id,
                  body: { params, ...(password !== undefined ? { secrets: { pass: password || null } } : {}) },
                  done: "Connection saved",
                },
                { onSuccess: () => setPassword(undefined) },
              )
            }
          >
            Save connection
          </Button>
        </div>
      </div>
    </Panel>
  );
}

function WebhookToken({ s }: { s: Detail }) {
  const client = useApiClient();
  const toast = useToast();
  const [token, setToken] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const qc = useQueryClient();
  const make = useMutation({
    mutationFn: () => data(Sensors.newToken({ client, path: { sid: s.id } })),
    onSuccess: (r) => {
      setAsking(false);
      setToken(r.token);
      void qc.invalidateQueries({ queryKey: ["sensor", s.id] });
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t make a token", body: e.message }),
  });
  return (
    <Panel
      title="Token"
      subtitle="Devices push readings with it. Making a new one stops the old one working."
      actions={
        <Button size="sm" icon={<KeyRound />} onClick={() => setAsking(true)}>
          New token
        </Button>
      }
    >
      <p className="text-[13px] text-fg-secondary">
        {s.has_token ? "A token is set." : "No token yet."} Push to{" "}
        <span className="font-mono">
          {typeof window === "undefined" ? "" : window.location.origin}/api/v1/sensors/push
        </span>{" "}
        with it as a bearer token, or put it in the address.
      </p>
      <Dialog
        open={asking}
        onOpenChange={setAsking}
        title="Make a new token?"
        description="Whatever sends with the old one stops getting through until you give it the new one."
        actions={
          <>
            <Button variant="ghost" onClick={() => setAsking(false)}>
              Cancel
            </Button>
            <Button variant="primary" disabled={make.isPending} onClick={() => make.mutate()}>
              Make new token
            </Button>
          </>
        }
      />
      <Dialog
        open={Boolean(token)}
        onOpenChange={(o) => !o && setToken(null)}
        wide
        title="New token"
        actions={
          <Button variant="primary" onClick={() => setToken(null)}>
            Done
          </Button>
        }
      >
        {token && <TokenReveal token={token} />}
      </Dialog>
    </Panel>
  );
}

function SettingsTab({ s }: { s: Detail }) {
  const { update } = useSensorActions();
  const hub = useSensors().data?.hub;
  const log = (s.streams ?? []).some((x) => x.kind === "log" || x.kind === "text") || s.type === "syslog";
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <div className="flex flex-col gap-4">
        <Panel title="About">
          <div className="flex flex-col gap-4">
            <NameField s={s} />
            <Field label="Namespace" hint="Who can see its digests, and where they go">
              {({ id }) => (
                <NamespaceSelect
                  id={id}
                  value={s.space}
                  onChange={(v) => update.mutate({ id: s.id, body: { space: v }, done: "Namespace changed" })}
                />
              )}
            </Field>
            {(s.type === "mqtt" || s.type === "syslog") && (
              <p className="text-[12.5px] text-fg-muted">
                {s.type === "mqtt" ? "Topics under " : "Sent from "}
                <span className="font-mono">{s.device}</span>, to{" "}
                <span className="font-mono">
                  {connectText(
                    s.type as StreamType,
                    typeof window === "undefined" ? "lens" : window.location.hostname,
                    hub ?? {},
                  )}
                </span>
                .
              </p>
            )}
          </div>
        </Panel>
        {s.type === "webhook" && <WebhookToken s={s} />}
        {s.type === "bridge" && <BridgeSettings s={s} />}
      </div>
      <Panel
        title="What it keeps"
        subtitle="Empty boxes follow Settings → Sensors. The hourly Tidy sensor data routine removes what’s past its time."
      >
        <HandlingEditor
          own={(s.own_handling ?? {}) as Handling}
          resolved={(s.handling ?? {}) as Handling}
          log={log}
          hasNamespace={s.space != null}
          busy={update.isPending}
          onSave={(change) => update.mutate({ id: s.id, body: { handling: change }, done: "Handling saved" })}
        />
      </Panel>
    </div>
  );
}

/** One sensor: its streams with charts and the latest readings, its kinds of log line, and its settings. */
export function SensorDetailPage({ id, tab }: { id: number; tab: "streams" | "log" | "settings" }) {
  const { admin, me } = useArchive();
  const router = useRouter();
  const sensor = useSensor(id);
  const { update, suggestion, remove } = useSensorActions();
  const [hours, setHours] = useState("168");
  const [deleting, setDeleting] = useState(false);
  const s = sensor.data;

  useEffect(() => {
    if (s?.family === "files") router.replace("/sources");
  }, [s?.family, router]);

  if (me && !admin)
    return (
      <EmptyState icon={<RadioTower />} title="Sensors are for admins">
        Admins connect devices and decide what Lens keeps from them.
      </EmptyState>
    );
  if (sensor.isLoading || !me) return <SkeletonRows rows={6} className="px-6 pt-6" />;
  if (sensor.error || !s)
    return (
      <EmptyState
        tone="error"
        icon={<RadioTower />}
        title="Couldn’t load the sensor"
        actions={
          <Button asChild>
            <Link href="/sensors">Back to sensors</Link>
          </Button>
        }
      >
        {(sensor.error as Error | null)?.message ?? "It may have been removed."}
      </EmptyState>
    );

  const streams = s.streams ?? [];
  const hasLog = streams.some((x) => x.kind === "log" || x.kind === "text");
  const current = tab === "log" && !hasLog ? "streams" : tab;
  const status = (s.status ?? "active") as Status;
  const setStatus = (v: Status, done: string) => update.mutate({ id: s.id, body: { status: v }, done });

  return (
    <div className="flex flex-col gap-4 px-4 pb-10 pt-[18px] md:px-6">
      <Link
        href="/sensors"
        className="inline-flex items-center gap-1.5 self-start text-[13px] text-fg-secondary hover:text-fg"
      >
        <ArrowLeft className="size-4" /> Sensors
      </Link>
      <div className="flex flex-wrap items-center gap-3">
        <SensorTile type={s.type} size={40} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2.5">
            <h1 className="truncate text-[24px] font-bold leading-tight tracking-[-.015em] text-fg">{s.name}</h1>
            <StatusBadge status={s.status} />
          </div>
          <p className="text-[13px] text-fg-secondary">
            {s.label}
            {s.namespace ? ` · ${s.namespace}` : ""} · {plural(streams.length, "stream")} ·{" "}
            {plural(s.readings ?? 0, "reading")} · last heard <DateTime iso={s.last_seen_at} />
          </p>
        </div>
        <div className="flex items-center gap-1.5">
          {status === "paused" || status === "ignored" ? (
            <Button size="sm" variant="secondary" onClick={() => setStatus("active", "Recording again")}>
              Resume
            </Button>
          ) : (
            <Button size="sm" variant="secondary" onClick={() => setStatus("paused", "Paused")}>
              Pause
            </Button>
          )}
          <Menu>
            <MenuTrigger asChild>
              <button
                type="button"
                aria-label={`More for ${s.name}`}
                className="grid size-8 place-items-center rounded-full text-fg-muted hover:bg-surface-neutral hover:text-fg"
              >
                <Ellipsis aria-hidden className="size-[18px]" />
              </button>
            </MenuTrigger>
            <MenuContent align="end">
              {status !== "ignored" && (
                <MenuItem onSelect={() => setStatus("ignored", "Ignored")}>Ignore (keep nothing, hide it)</MenuItem>
              )}
              <MenuSeparator />
              <MenuItem danger onSelect={() => setDeleting(true)}>
                Remove…
              </MenuItem>
            </MenuContent>
          </Menu>
        </div>
      </div>
      {status === "new" && (
        <Banner
          title="New sensor."
          action={
            <Button
              size="xs"
              variant="primary"
              icon={<Check />}
              disabled={suggestion.isPending}
              onClick={() => suggestion.mutate({ id: s.id, name: s.name })}
            >
              Apply suggestion
            </Button>
          }
        >
          {s.suggested?.reason ?? "Choose how to handle it in Settings below."}
        </Banner>
      )}
      {status === "paused" && <Banner tone="warning">{STATUS.paused.hint}.</Banner>}
      <Tabs
        aria-label="Sensor"
        value={current}
        items={[
          { value: "streams", label: "Streams", count: streams.length, href: `/sensors/${s.id}` },
          ...(hasLog ? [{ value: "log", label: "Log lines", href: `/sensors/${s.id}?tab=log` }] : []),
          { value: "settings", label: "Settings", href: `/sensors/${s.id}?tab=settings` },
        ]}
      />
      {current === "streams" &&
        (streams.length ? (
          <>
            <div className="flex items-center gap-3">
              <span className="flex-1 text-[13px] text-fg-secondary">Charts show hourly averages.</span>
              <Segmented label="Range" value={hours} onChange={setHours} items={RANGES} />
            </div>
            <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {streams.map((st) => (
                <StreamCard key={st.id} sid={s.id} st={st} hours={Number(hours)} />
              ))}
            </ul>
            <ReadingsTable sid={s.id} streams={streams} />
          </>
        ) : (
          <EmptyState icon={<RadioTower />} title="Nothing has arrived yet">
            {s.type === "webhook"
              ? "Push a reading with its token (Settings tab) and it shows up here."
              : s.type === "bridge"
                ? "Devices on the other broker show up as sensors of their own once they publish."
                : "Readings show up here as soon as it sends one."}
          </EmptyState>
        ))}
      {current === "log" && <PatternsTab sid={s.id} />}
      {current === "settings" && <SettingsTab s={s} />}
      <Dialog
        open={deleting}
        onOpenChange={setDeleting}
        title={`Remove “${s.name}”?`}
        description="Everything it kept goes too: readings, summaries and kinds of log line. Digests already written stay. A device that sends again shows up as new."
        actions={
          <>
            <Button variant="ghost" onClick={() => setDeleting(false)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              disabled={remove.isPending}
              onClick={() => remove.mutate({ id: s.id, name: s.name }, { onSuccess: () => router.push("/sensors") })}
            >
              Remove sensor
            </Button>
          </>
        }
      />
    </div>
  );
}
