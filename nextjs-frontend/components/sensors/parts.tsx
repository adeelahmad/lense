"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Cable, type LucideIcon, RadioTower, ScrollText, Webhook } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Sensors } from "@/app/openapi-client";
import type { HubStatus, SensorType } from "@/app/openapi-client/types.gen";
import {
  connectText,
  curlExample,
  daysText,
  handlingChange,
  parseDays,
  pushUrl,
  STATUS,
  STORE_OPTIONS,
  STREAM_TYPES,
  type Handling,
  type Status,
  type StreamType,
} from "@/components/sensors/sensor-model";
import { TypeTile } from "@/components/sources/source-icons";
import type { SourceType } from "@/components/sources/source-model";
import { Badge } from "@/components/ui/badge";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, SecretField, Select, Switch } from "@/components/ui/field";
import { CodeBlock } from "@/components/ui/states";
import { Tabs } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

export const STREAM_ICON: Record<StreamType, LucideIcon> = {
  mqtt: RadioTower,
  syslog: ScrollText,
  webhook: Webhook,
  bridge: Cable,
};

/** A sensor's kind in a rounded tile: the stream kinds here, the file kinds from Sources. */
export function SensorTile({ type, size = 32 }: { type: string; size?: number }) {
  const Icon = STREAM_ICON[type as StreamType];
  if (!Icon) return <TypeTile type={type as SourceType} size={size} />;
  return (
    <span
      aria-hidden
      className="grid shrink-0 place-items-center rounded-[9px] bg-blue-surface text-fg-accent"
      style={{ width: size, height: size }}
    >
      <Icon style={{ width: size * 0.53, height: size * 0.53 }} />
    </span>
  );
}

export function StatusBadge({ status }: { status?: string | null }) {
  const s = STATUS[(status ?? "active") as Status] ?? STATUS.active;
  return (
    <Badge tone={s.tone} dot>
      {s.label}
    </Badge>
  );
}

/** Sensors · Sources · Hub logins, for admins. */
export function SensorsTabs({ tab, newCount }: { tab: "sensors" | "sources" | "logins"; newCount?: number }) {
  return (
    <Tabs
      aria-label="Sensors"
      value={tab}
      items={[
        { value: "sensors", label: "Sensors", count: newCount ? `${newCount} new` : undefined, href: "/sensors" },
        { value: "sources", label: "Files, email and calendars", href: "/sources" },
        { value: "logins", label: "Hub logins", href: "/sensors/logins" },
      ]}
    />
  );
}

/** A namespace picker over the archive's namespaces; empty is none. */
export function NamespaceSelect({
  value,
  onChange,
  id,
  none = "No namespace",
  size,
}: {
  value: number | null | undefined;
  onChange: (v: number | null) => void;
  id?: string;
  none?: string;
  size?: "sm" | "md";
}) {
  const { namespaces } = useArchive();
  return (
    <Select
      id={id}
      size={size}
      value={value == null ? "" : String(value)}
      onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}
      options={[{ value: "", label: none }, ...namespaces.map((n) => ({ value: String(n.id), label: n.name }))]}
    />
  );
}

/** A small line chart of hourly values; gaps where nothing arrived. */
export function Sparkline({
  path,
  w = 120,
  h = 28,
  className,
}: {
  path: string;
  w?: number;
  h?: number;
  className?: string;
}) {
  if (!path) return <span className={cn("text-[12px] text-fg-muted", className)}>no summary yet</span>;
  return (
    <svg
      viewBox={`-1 -2 ${w + 2} ${h + 4}`}
      width={w}
      height={h}
      aria-hidden
      className={cn("overflow-visible text-blue", className)}
    >
      <path d={path} fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

type HandlingForm = {
  store: string;
  raw_days: string;
  rollup_days: string;
  important_days: string;
  max_per_minute: string;
  triage: boolean | null;
  digest: boolean | null;
};

function toForm(own: Handling): HandlingForm {
  const s = (v: number | null | undefined) => (v == null ? "" : String(v));
  return {
    store: own.store ?? "",
    raw_days: s(own.raw_days),
    rollup_days: s(own.rollup_days),
    important_days: s(own.important_days),
    max_per_minute: s(own.max_per_minute),
    triage: own.triage ?? null,
    digest: own.digest ?? null,
  };
}

/**
 * What a sensor keeps and for how long. Empty boxes follow Settings → Sensors; 0 days keeps them for good. Saving
 * sends only what changed.
 */
export function HandlingEditor({
  own,
  resolved,
  log,
  hasNamespace,
  busy,
  onSave,
}: {
  own: Handling;
  resolved: Handling;
  log: boolean;
  hasNamespace: boolean;
  busy?: boolean;
  onSave: (change: Handling) => void;
}) {
  const [f, setF] = useState<HandlingForm>(() => toForm(own));
  const ownKey = JSON.stringify(own);
  useEffect(() => setF(toForm(JSON.parse(ownKey) as Handling)), [ownKey]);
  const days = {
    raw_days: parseDays(f.raw_days),
    rollup_days: parseDays(f.rollup_days),
    important_days: parseDays(f.important_days),
    max_per_minute: f.max_per_minute.trim()
      ? /^\d+$/.test(f.max_per_minute.trim())
        ? Number(f.max_per_minute)
        : "bad"
      : null,
  } as const;
  const bad = Object.entries(days).find(([, v]) => v === "bad")?.[0];
  const perMinuteBad =
    typeof days.max_per_minute === "number" && (days.max_per_minute < 1 || days.max_per_minute > 100000);
  const form: Handling = {
    store: (f.store || null) as Handling["store"],
    raw_days: days.raw_days === "bad" ? null : days.raw_days,
    rollup_days: days.rollup_days === "bad" ? null : days.rollup_days,
    important_days: days.important_days === "bad" ? null : days.important_days,
    max_per_minute: days.max_per_minute === "bad" ? null : days.max_per_minute,
    triage: f.triage,
    digest: f.digest,
  };
  const change = handlingChange(own, form);
  const dirty = Object.keys(change).length > 0;
  const set = (k: keyof HandlingForm, v: unknown) => setF((x) => ({ ...x, [k]: v }));
  const fallback = (k: "raw_days" | "rollup_days" | "important_days") =>
    own[k] == null ? `Default: ${daysText(resolved[k])}` : "Default from settings";
  const dayField = (k: "raw_days" | "rollup_days" | "important_days", label: string, hint: string) => (
    <Field
      label={label}
      hint={days[k] === "bad" ? undefined : hint}
      error={days[k] === "bad" ? "A whole number of days" : undefined}
    >
      {({ id, describedBy, invalid }) => (
        <Input
          id={id}
          aria-describedby={describedBy}
          invalid={invalid}
          inputMode="numeric"
          value={f[k]}
          placeholder={fallback(k)}
          onChange={(e) => set(k, e.target.value)}
        />
      )}
    </Field>
  );
  return (
    <div className="flex flex-col gap-4">
      <Field label="Keep" hint={STORE_OPTIONS.find((o) => o.value === (f.store || resolved.store))?.hint}>
        {({ id }) => (
          <Select
            id={id}
            value={f.store}
            onChange={(e) => set("store", e.target.value)}
            options={[
              {
                value: "",
                label: own.store
                  ? "Default from settings"
                  : `Default (${(STORE_OPTIONS.find((o) => o.value === resolved.store) ?? STORE_OPTIONS[0]).label.toLowerCase()})`,
              },
              ...STORE_OPTIONS.map((o) => ({ value: o.value, label: o.label })),
            ]}
          />
        )}
      </Field>
      <div className="grid gap-3 sm:grid-cols-3">
        {dayField("raw_days", "Readings for (days)", "0 keeps them for good")}
        {dayField("rollup_days", "Hourly summaries for (days)", "Charts use these")}
        {log && dayField("important_days", "Warnings and errors for (days)", "When longer than readings")}
      </div>
      <Field
        label="Most readings a minute, per stream"
        error={bad === "max_per_minute" || perMinuteBad ? "A whole number from 1 to 100,000" : undefined}
        hint="More than this are dropped and counted, so a chatty device can’t fill the disk"
      >
        {({ id, describedBy, invalid }) => (
          <Input
            id={id}
            aria-describedby={describedBy}
            invalid={invalid}
            inputMode="numeric"
            className="sm:max-w-[200px]"
            value={f.max_per_minute}
            placeholder={own.max_per_minute == null ? `Default: ${resolved.max_per_minute ?? "–"}` : "Default"}
            onChange={(e) => set("max_per_minute", e.target.value)}
          />
        )}
      </Field>
      {log && (
        <Switch
          checked={f.triage ?? Boolean(resolved.triage)}
          onCheckedChange={(v) => set("triage", v)}
          label="Let the decision model sort new kinds of log line into routine, notable and alert"
        />
      )}
      <div className="flex flex-col gap-1">
        <Switch
          checked={Boolean(f.digest)}
          disabled={!hasNamespace && !f.digest}
          onCheckedChange={(v) => set("digest", v)}
          label="Write a daily digest into its namespace"
        />
        <span className="pl-[46px] text-[12.5px] text-fg-muted">
          {hasNamespace
            ? "A short document each day: ranges, the busiest log lines, warnings. It is searchable like any recording."
            : "Give the sensor a namespace first."}
        </span>
      </div>
      <div className="flex items-center gap-2">
        <Button
          size="sm"
          variant="primary"
          disabled={!dirty || Boolean(bad) || perMinuteBad || busy}
          onClick={() => onSave(change)}
        >
          Save handling
        </Button>
        {dirty && (
          <Button size="sm" variant="ghost" onClick={() => setF(toForm(own))}>
            Undo changes
          </Button>
        )}
      </div>
    </div>
  );
}

const PARAM_LABEL: Record<string, { label: string; placeholder?: string; hint?: string }> = {
  prefix: { label: "Topic prefix", placeholder: "zigbee2mqtt/kitchen", hint: "Topics under it are its streams" },
  address: { label: "IP address it sends from", placeholder: "192.168.1.1" },
  host: { label: "Broker host", placeholder: "homeassistant.local" },
  port: { label: "Port", placeholder: "1883" },
  topics: { label: "Topics", placeholder: "zigbee2mqtt/#, tele/#", hint: "Comma-separated; # is everything" },
  user: { label: "Username", placeholder: "optional" },
};

/** A bridge's connection fields (host, port, TLS, topics, user), shared by the add dialog and a bridge's page. */
export function BridgeFields({
  params,
  onChange,
  password,
  onPassword,
  passwordSet,
}: {
  params: Record<string, string>;
  onChange: (p: Record<string, string>) => void;
  password: string | undefined;
  onPassword: (v: string | undefined) => void;
  passwordSet: boolean;
}) {
  const set = (k: string, v: string) => onChange({ ...params, [k]: v });
  const text = (k: string) => (
    <Field label={PARAM_LABEL[k].label} hint={PARAM_LABEL[k].hint}>
      {({ id, describedBy }) => (
        <Input
          id={id}
          aria-describedby={describedBy}
          mono
          value={params[k] ?? ""}
          placeholder={PARAM_LABEL[k].placeholder}
          onChange={(e) => set(k, e.target.value)}
        />
      )}
    </Field>
  );
  return (
    <>
      <div className="grid gap-3 sm:grid-cols-[1fr_120px]">
        {text("host")}
        {text("port")}
      </div>
      <Switch checked={params.tls === "true"} onCheckedChange={(v) => set("tls", v ? "true" : "false")} label="TLS" />
      {text("topics")}
      <div className="grid gap-3 sm:grid-cols-2">
        {text("user")}
        <Field label="Password">
          {({ id }) => <SecretField id={id} isSet={passwordSet} value={password} onChange={onPassword} />}
        </Field>
      </div>
    </>
  );
}

/** A webhook's address and token, shown once after it's made or a new token is asked for. */
export function TokenReveal({ token }: { token: string }) {
  const origin = typeof window === "undefined" ? "" : window.location.origin;
  return (
    <div className="flex flex-col gap-3">
      <Banner tone="warning">Copy these now: the token isn’t shown again. You can make a new one later.</Banner>
      <Field label="Address with the token in it">
        {() => <CodeBlock text={pushUrl(origin, token)} label="address" />}
      </Field>
      <Field label="Or send the token as a bearer token to">
        {() => <CodeBlock text={`${origin}/api/v1/sensors/push`} label="address" />}
      </Field>
      <Field label="Token">{() => <CodeBlock text={token} label="token" />}</Field>
      <Field label="Try it" hint="Add /name to the address to name the stream; JSON and one value per line work too">
        {() => <CodeBlock text={curlExample(origin, token)} label="example" />}
      </Field>
    </div>
  );
}

/** Add a stream sensor: a webhook (its token shown once), an MQTT device or syslog sender up front, or a bridge. */
export function AddSensorDialog({
  open,
  onOpenChange,
  types,
  hub,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  types: Record<string, SensorType>;
  hub: HubStatus;
}) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const router = useRouter();
  const [type, setType] = useState<StreamType>("webhook");
  const [name, setName] = useState("");
  const [params, setParams] = useState<Record<string, string>>({});
  const [password, setPassword] = useState<string | undefined>();
  const [space, setSpace] = useState<number | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const reset = () => {
    setName("");
    setParams({});
    setPassword(undefined);
    setSpace(null);
    setToken(null);
  };
  const pick = (t: StreamType) => {
    setType(t);
    setParams(t === "bridge" ? { port: "1883", tls: "false", topics: "#" } : {});
  };
  const create = useMutation({
    mutationFn: () =>
      data(
        Sensors.createSensor({
          client,
          body: {
            type,
            name: name.trim() || null,
            params,
            space,
            secrets: type === "bridge" && password ? { pass: password } : null,
          },
        }),
      ),
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: ["sensors"] });
      if (r.token) setToken(r.token);
      else {
        toast({ tone: "green", title: "Sensor added" });
        onOpenChange(false);
        reset();
        router.push(`/sensors/${r.id}`);
      }
    },
  });
  const host = typeof window === "undefined" ? "lens" : window.location.hostname;
  const close = (o: boolean) => {
    onOpenChange(o);
    if (!o) {
      reset();
      create.reset();
    }
  };
  if (token)
    return (
      <Dialog
        open={open}
        onOpenChange={close}
        wide
        title="Webhook added"
        actions={
          <Button variant="primary" onClick={() => close(false)}>
            Done
          </Button>
        }
      >
        <TokenReveal token={token} />
      </Dialog>
    );
  return (
    <Dialog
      open={open}
      onOpenChange={close}
      wide
      title="Add a sensor"
      description="Devices that talk to the hub show up by themselves; add one here to give it a namespace first, or to make a webhook or bridge."
      actions={
        <>
          <Button variant="ghost" onClick={() => close(false)}>
            Cancel
          </Button>
          <Button variant="primary" disabled={create.isPending} onClick={() => create.mutate()}>
            {type === "webhook" ? "Make webhook" : "Add sensor"}
          </Button>
        </>
      }
    >
      <div role="radiogroup" aria-label="Kind of sensor" className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {STREAM_TYPES.map((t) => (
          <button
            key={t}
            type="button"
            role="radio"
            aria-checked={type === t}
            onClick={() => pick(t)}
            className={cn(
              "flex flex-col items-start gap-2 rounded-md border p-3 text-left transition-colors",
              type === t ? "border-blue bg-blue-surface" : "border-border hover:bg-surface",
            )}
          >
            <SensorTile type={t} size={28} />
            <span className="text-[13.5px] font-bold text-fg">{types[t]?.label ?? t}</span>
          </button>
        ))}
      </div>
      {types[type]?.help && <p className="text-[13px] text-fg-secondary">Use this for {types[type].help}.</p>}
      {type !== "webhook" && type !== "bridge" && (
        <Banner>
          Point it at <span className="font-mono">{connectText(type, host, hub)}</span>
          {!hub.enabled && ". The hub is off: turn it on in Settings → Sensors"}.
        </Banner>
      )}
      <Field label="Name" optional>
        {({ id }) => (
          <Input id={id} value={name} onChange={(e) => setName(e.target.value)} placeholder="Kitchen sensor" />
        )}
      </Field>
      {type === "mqtt" && (
        <Field label={PARAM_LABEL.prefix.label} hint={PARAM_LABEL.prefix.hint}>
          {({ id, describedBy }) => (
            <Input
              id={id}
              aria-describedby={describedBy}
              mono
              value={params.prefix ?? ""}
              placeholder={PARAM_LABEL.prefix.placeholder}
              onChange={(e) => setParams({ prefix: e.target.value })}
            />
          )}
        </Field>
      )}
      {type === "syslog" && (
        <Field label={PARAM_LABEL.address.label}>
          {({ id }) => (
            <Input
              id={id}
              mono
              value={params.address ?? ""}
              placeholder={PARAM_LABEL.address.placeholder}
              onChange={(e) => setParams({ address: e.target.value })}
            />
          )}
        </Field>
      )}
      {type === "bridge" && (
        <BridgeFields
          params={params}
          onChange={setParams}
          password={password}
          onPassword={setPassword}
          passwordSet={false}
        />
      )}
      <Field
        label="Namespace"
        hint={
          type === "bridge" ? "Devices it brings in start in this namespace" : "Where its digests go, and who sees it"
        }
      >
        {({ id }) => <NamespaceSelect id={id} value={space} onChange={setSpace} />}
      </Field>
      {create.isError && <Banner tone="error">{create.error.message}</Banner>}
    </Dialog>
  );
}
