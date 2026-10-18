"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { Scaling } from "@/app/openapi-client";
import type { DriverField, DriverSpec, Group, Provider } from "@/app/openapi-client/types.gen";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Checkbox, Field, Input, Select, Textarea } from "@/components/ui/field";
import { SkeletonRows } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { cn } from "@/lib/utils";

/** One of the API's reasons as a sentence: capitalised, with a full stop. */
function sentence(s: string): string {
  const t = s.trim();
  return t ? `${t.charAt(0).toUpperCase()}${t.slice(1)}${/[.!?]$/.test(t) ? "" : "."}` : t;
}

/** What approving a group binds, in words: how many units at most, where, and what they may cost. */
export function termsText(g: Group, providers: Provider[]): string {
  const p = providers.find((x) => x.id === g.provider);
  const where = p ? ` on ${p.name}` : "";
  const usd = g.budget?.usd;
  const cost = usd != null ? `, up to $${usd} a month` : p && !p.priced ? ", at no cost" : "";
  const cap = g.at_cap === "min" && usd != null ? ` (at the cap, ${g.min} keep running)` : "";
  return `Up to ${g.max} unit${g.max === 1 ? "" : "s"}${where}${cost}${cap}`;
}

/** Where a group stands: its units now, and why it launches nothing when it doesn't. */
export function groupState(g: Group): string {
  const u = g.units;
  const parts = [`${u.running} of ${g.max} running`];
  if (u.launching) parts.push(`${u.launching} joining`);
  if (u.draining) parts.push(`${u.draining} leaving`);
  return parts.join(", ") + (g.waiting_for ? `. Waiting for ${g.waiting_for}` : "");
}

/** The scaling groups: each one's terms and units, approving its terms (a person's pick, here), pausing it. */
export function ScalingGroups() {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const groups = useQuery({
    queryKey: ["scaling-groups"],
    queryFn: () => data(Scaling.listGroups({ client })),
    refetchInterval: 15_000,
  });
  const providers = useQuery({
    queryKey: ["scaling-providers"],
    queryFn: () => data(Scaling.listProviders({ client })),
  });
  const fail = (title: string) => (e: Error) => toast({ title, body: e.message, tone: "red" });
  const done = () => void qc.invalidateQueries({ queryKey: ["scaling-groups"] });
  const approve = useMutation({
    mutationFn: (gid: number) => data(Scaling.approveGroup({ client, path: { gid } })),
    onSuccess: (g) => {
      done();
      toast({ title: `Approved ${g.name}`, tone: "green" });
    },
    onError: fail("Couldn’t approve the group"),
  });
  const pause = useMutation({
    mutationFn: ({ gid, paused }: { gid: number; paused: boolean }) =>
      data(Scaling.pauseGroup({ client, path: { gid }, body: { paused } })),
    onSuccess: done,
    onError: fail("Couldn’t change the group"),
  });
  if (groups.isPending) return <SkeletonRows rows={2} />;
  if (groups.error)
    return (
      <Banner tone="error" title="Couldn’t read the scaling groups.">
        {groups.error.message}
      </Banner>
    );
  const list = groups.data;
  if (!list.length)
    return (
      <p className="text-[12.5px] leading-[1.45] text-fg-secondary">
        No scaling groups yet. Make one with New group on a provider above, or ask the assistant to set one up (or make
        one over the API or MCP); it shows here for you to approve.
      </p>
    );
  return (
    <ul aria-label="Scaling groups" className="flex flex-col divide-y divide-border rounded-md border border-border">
      {list.map((g) => (
        <li key={g.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-3 py-2.5 text-[13px]">
          <div className="min-w-0 flex-1">
            <div className="font-semibold text-fg">{g.name}</div>
            <div className="text-fg-secondary">{termsText(g, providers.data ?? [])}</div>
            <div className="text-[12.5px] text-fg-secondary">{sentence(groupState(g))}</div>
          </div>
          <div className="flex gap-2">
            {!g.approved && (
              <Button
                size="xs"
                variant="approve"
                aria-label={`Approve ${g.name}: ${termsText(g, providers.data ?? [])}`}
                disabled={approve.isPending}
                onClick={() => approve.mutate(g.id)}
              >
                Approve
              </Button>
            )}
            <Button
              size="xs"
              variant="ghost"
              disabled={pause.isPending}
              onClick={() => pause.mutate({ gid: g.id, paused: !g.paused })}
            >
              {g.paused ? "Resume" : "Pause"}
            </Button>
          </div>
        </li>
      ))}
    </ul>
  );
}

type Fields = Record<string, DriverField>;
/** What a person has typed for a driver's fields: text, a checkbox, or the options ticked. */
export type SpecForm = Record<string, string | boolean | string[]>;

const picks = (f: DriverField) => (f.options ?? []).map(String);
const checkOf = (p: Provider) => p.check as { ok?: boolean; error?: string } | null | undefined;

/** A driver's fields as a form, with their defaults filled in. */
export function specForm(fields: Fields): SpecForm {
  return Object.fromEntries(
    Object.entries(fields).map(([k, f]) => {
      const d = f.default;
      if (f.type === "boolean") return [k, d === true];
      if (f.type === "list")
        return [k, f.options?.length ? (Array.isArray(d) ? d.map(String) : []) : Array.isArray(d) ? d.join("\n") : ""];
      return [k, d == null ? "" : String(d)];
    }),
  );
}

/** The form as the API takes it: settings (or a template) and, apart, the secrets; or what's wrong, by field. A field
 * left empty is left out, so the driver's default applies. */
export function specValues(
  fields: Fields,
  form: SpecForm,
): { values: Record<string, unknown>; secrets: Record<string, string>; errors: Record<string, string> } {
  const values: Record<string, unknown> = {};
  const secrets: Record<string, string> = {};
  const errors: Record<string, string> = {};
  for (const [k, f] of Object.entries(fields)) {
    const raw = form[k];
    let v: unknown;
    if (f.type === "boolean") v = raw === true;
    else if (f.type === "list") {
      const xs = Array.isArray(raw)
        ? raw
        : String(raw ?? "")
            .split(/[\n,]/)
            .map((x) => x.trim())
            .filter(Boolean);
      v = xs.length ? xs : undefined;
    } else {
      const t = String(raw ?? "").trim();
      if (t && (f.type === "integer" || f.type === "number")) {
        const n = Number(t);
        if (f.type === "integer" ? !Number.isInteger(n) : !Number.isFinite(n)) {
          errors[k] = f.type === "integer" ? "A whole number." : "A number.";
          continue;
        }
        v = n;
      } else v = t || undefined;
    }
    if (v === undefined) {
      if (f.required) errors[k] = `${f.label} is needed.`;
    } else if (f.secret) secrets[k] = String(v);
    else values[k] = v;
  }
  return { values, secrets, errors };
}

/** A driver's fields, drawn from its spec: secrets as passwords, lists one per line (or ticked from their options),
 * booleans as checkboxes, a choice of options as a select. */
export function SpecFields({
  fields,
  form,
  onChange,
  errors,
}: {
  fields: Fields;
  form: SpecForm;
  onChange: (f: SpecForm) => void;
  errors: Record<string, string>;
}) {
  const set = (k: string, v: string | boolean | string[]) => onChange({ ...form, [k]: v });
  return (
    <>
      {Object.entries(fields).map(([k, f]) => {
        const help = f.help ? sentence(f.help) : undefined;
        if (f.type === "boolean")
          return (
            <div key={k} className="flex flex-col gap-1">
              <Checkbox checked={form[k] === true} onCheckedChange={(v) => set(k, v)} label={f.label} />
              {help && <p className="text-[12.5px] leading-snug text-fg-muted">{help}</p>}
            </div>
          );
        if (f.type === "list" && f.options?.length) {
          const on = Array.isArray(form[k]) ? form[k] : [];
          return (
            <fieldset key={k} className="flex flex-col gap-1.5">
              <legend className="mb-1.5 text-[13px] font-bold leading-tight text-fg-strong">
                {f.label}
                {!f.required && <span className="ml-1 font-normal text-fg-muted">optional</span>}
              </legend>
              <div className="flex flex-wrap gap-x-4 gap-y-2">
                {picks(f).map((o) => (
                  <Checkbox
                    key={o}
                    label={o}
                    checked={on.includes(o)}
                    onCheckedChange={(v) => set(k, v ? [...on, o] : on.filter((x) => x !== o))}
                  />
                ))}
              </div>
              {(errors[k] || help) && (
                <p
                  role={errors[k] ? "alert" : undefined}
                  className={cn("text-[12.5px] leading-snug", errors[k] ? "text-red-dark" : "text-fg-muted")}
                >
                  {errors[k] || help}
                </p>
              )}
            </fieldset>
          );
        }
        const hint =
          f.type === "list"
            ? `One per line.${help ? ` ${help}` : ""}`
            : f.secret
              ? `${help ? `${help} ` : ""}Sealed when saved, never shown again.`
              : help;
        return (
          <Field key={k} label={f.label} optional={!f.required} hint={hint} error={errors[k]}>
            {({ id, describedBy, invalid }) =>
              f.type === "list" ? (
                <Textarea
                  id={id}
                  aria-describedby={describedBy}
                  invalid={invalid}
                  rows={3}
                  className="min-h-[64px]"
                  value={String(form[k] ?? "")}
                  onChange={(e) => set(k, e.target.value)}
                  spellCheck={false}
                />
              ) : f.options?.length ? (
                <Select
                  id={id}
                  aria-describedby={describedBy}
                  invalid={invalid}
                  value={String(form[k] ?? "")}
                  onChange={(e) => set(k, e.target.value)}
                  options={[...(f.required ? [] : [{ value: "", label: "Default" }]), ...picks(f)]}
                />
              ) : (
                <Input
                  id={id}
                  aria-describedby={describedBy}
                  invalid={invalid}
                  type={f.secret ? "password" : f.type === "string" ? "text" : "number"}
                  autoComplete={f.secret ? "new-password" : "off"}
                  spellCheck={false}
                  value={String(form[k] ?? "")}
                  onChange={(e) => set(k, e.target.value)}
                />
              )
            }
          </Field>
        );
      })}
    </>
  );
}

/** A provider's last check, in words. */
export function checkText(p: Provider): string {
  const c = checkOf(p);
  if (!c) return "Not checked yet";
  return c.ok ? "Check passed" : `Check failed: ${sentence(String(c.error ?? "no reason given"))}`;
}

/** What a driver needs fetched, and its licence, when it needs anything. */
function fetchNote(d: DriverSpec): string | null {
  if (!d.extra) return null;
  const lic = d.license ? ` (${d.license})` : "";
  return d.ready
    ? `Uses the ${d.extra} packages${lic}, already installed.`
    : `Needs the ${d.extra} packages${lic}, fetched the first time you save one.`;
}

const UNDO_MS = 6000;

/** The providers units launch on: each one's driver and last check, checking again, removing (with Undo), adding
 * one, and making a group on one. */
export function ScalingProviders() {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const [adding, setAdding] = useState(false);
  const [groupOn, setGroupOn] = useState<Provider | null>(null);
  const [hidden, setHidden] = useState<number[]>([]);
  const pending = useRef(new Map<number, ReturnType<typeof setTimeout>>());
  const providers = useQuery({
    queryKey: ["scaling-providers"],
    queryFn: () => data(Scaling.listProviders({ client })),
  });
  const drivers = useQuery({
    queryKey: ["scaling-drivers"],
    queryFn: () => data(Scaling.listDrivers({ client })),
  });
  const groups = useQuery({
    queryKey: ["scaling-groups"],
    queryFn: () => data(Scaling.listGroups({ client })),
    refetchInterval: 15_000,
  });
  const refresh = () => void qc.invalidateQueries({ queryKey: ["scaling-providers"] });
  const check = useMutation({
    mutationFn: (p: Provider) => data(Scaling.checkProvider({ client, path: { pid: p.id } })),
    onSuccess: (c, p) => {
      refresh();
      toast(
        c.ok
          ? { title: `${p.name}: check passed`, tone: "green" }
          : { title: `${p.name}: check failed`, body: sentence(String(c.error ?? "")), tone: "red" },
      );
    },
    onError: (e: Error) => toast({ title: "Couldn’t check the provider", body: e.message, tone: "red" }),
  });
  // A removal waits a few seconds for Undo, since its sealed credentials can't be put back once it's gone; leaving the
  // page removes whatever is waiting at once.
  const drop = (pid: number, name: string) =>
    void data(Scaling.removeProvider({ client, path: { pid } }))
      .then(refresh)
      .catch((e: Error) => {
        setHidden((h) => h.filter((x) => x !== pid));
        toast({ title: `Couldn’t remove ${name}`, body: e.message, tone: "red" });
      });
  const dropRef = useRef(drop);
  useEffect(() => {
    dropRef.current = drop;
  });
  useEffect(() => {
    const waiting = pending.current;
    return () => {
      for (const [pid, t] of waiting) {
        clearTimeout(t);
        dropRef.current(pid, "the provider");
      }
      waiting.clear();
    };
  }, []);
  const remove = (p: Provider) => {
    setHidden((h) => [...h, p.id]);
    const t = setTimeout(() => {
      pending.current.delete(p.id);
      drop(p.id, p.name);
    }, UNDO_MS);
    pending.current.set(p.id, t);
    toast({
      title: `Removed ${p.name}`,
      duration: UNDO_MS,
      action: {
        label: "Undo",
        onClick: () => {
          clearTimeout(t);
          pending.current.delete(p.id);
          setHidden((h) => h.filter((x) => x !== p.id));
        },
      },
    });
  };

  if (providers.isPending) return <SkeletonRows rows={1} />;
  if (providers.error)
    return (
      <Banner tone="error" title="Couldn’t read the providers.">
        {providers.error.message}
      </Banner>
    );
  const specs = drivers.data ?? [];
  const spec = (p: Provider) => specs.find((d) => d.id === p.driver);
  const list = providers.data.filter((p) => !hidden.includes(p.id));
  return (
    <div className="flex flex-col gap-3">
      {list.length ? (
        <ul aria-label="Providers" className="flex flex-col divide-y divide-border rounded-md border border-border">
          {list.map((p) => {
            const d = spec(p);
            const set = Object.keys(p.secrets ?? {}).map((k) => d?.fields[k]?.label ?? k);
            const users = (groups.data ?? []).filter((g) => g.provider === p.id).map((g) => g.name);
            return (
              <li key={p.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-3 py-2.5 text-[13px]">
                <div className="min-w-0 flex-1">
                  <div className="font-semibold text-fg">{p.name}</div>
                  <div className="text-fg-secondary">
                    {p.label ?? d?.label ?? p.driver}
                    {set.length ? ` · ${set.join(", ")} set` : ""}
                  </div>
                  <div
                    className={cn("text-[12.5px]", checkOf(p)?.ok === false ? "text-red-dark" : "text-fg-secondary")}
                  >
                    {checkText(p)}
                  </div>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button size="xs" onClick={() => setGroupOn(p)} aria-label={`New group on ${p.name}`}>
                    New group
                  </Button>
                  <Button
                    size="xs"
                    variant="ghost"
                    disabled={check.isPending}
                    onClick={() => check.mutate(p)}
                    aria-label={`Check ${p.name} again`}
                  >
                    Check again
                  </Button>
                  <Button
                    size="xs"
                    variant="danger-ghost"
                    disabled={users.length > 0}
                    disabledReason={
                      users.length
                        ? `${users.join(", ")} use${users.length === 1 ? "s" : ""} it: remove ${users.length === 1 ? "that group" : "those groups"} first`
                        : undefined
                    }
                    onClick={() => remove(p)}
                    aria-label={`Remove ${p.name}`}
                  >
                    Remove
                  </Button>
                </div>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="text-[12.5px] leading-[1.45] text-fg-secondary">
          No providers yet. A provider is where a group’s units launch; This machine runs more workers here at no cost.
        </p>
      )}
      <Button size="sm" className="self-start" disabled={!drivers.data} onClick={() => setAdding(true)}>
        Add a provider
      </Button>
      {adding && <AddProvider drivers={specs} onClose={() => setAdding(false)} />}
      {groupOn && <NewGroup provider={groupOn} driver={spec(groupOn)} onClose={() => setGroupOn(null)} />}
    </div>
  );
}

/** Add a provider: pick a driver, then fill in its fields; the check it runs on saving is shown. */
export function AddProvider({ drivers, onClose }: { drivers: DriverSpec[]; onClose: () => void }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const [driver, setDriver] = useState<DriverSpec | null>(drivers.length === 1 ? drivers[0] : null);
  const [name, setName] = useState(driver?.label ?? "");
  const [form, setForm] = useState<SpecForm>(() => (driver ? specForm(driver.fields) : {}));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState<Provider | null>(null);
  const nameRef = useRef<HTMLInputElement>(null);
  const pick = (d: DriverSpec) => {
    setDriver(d);
    setName(d.label);
    setForm(specForm(d.fields));
    setErrors({});
  };
  useEffect(() => nameRef.current?.focus(), [driver]);
  const add = useMutation({
    mutationFn: (body: {
      name: string;
      driver: string;
      settings: Record<string, unknown>;
      secrets: Record<string, string>;
    }) => data(Scaling.addProvider({ client, body })),
    onSuccess: (p) => {
      void qc.invalidateQueries({ queryKey: ["scaling-providers"] });
      void qc.invalidateQueries({ queryKey: ["scaling-drivers"] });
      if (checkOf(p)?.ok === false) return setSaved(p);
      toast({ title: `Added ${p.name}`, body: checkOf(p)?.ok ? "Its check passed." : undefined, tone: "green" });
      onClose();
    },
  });
  const save = () => {
    if (!driver) return;
    const { values, secrets, errors: bad } = specValues(driver.fields, form);
    if (!name.trim()) bad.name = "A name is needed.";
    setErrors(bad);
    if (Object.keys(bad).length) return;
    add.mutate({ name: name.trim(), driver: driver.id, settings: values, secrets });
  };
  const note = driver && fetchNote(driver);
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={saved ? `Saved ${saved.name}` : driver ? `Add ${driver.label}` : "Add a provider"}
      description={saved ? undefined : driver ? driver.about : "Where a scaling group’s units launch."}
      actions={
        saved ? (
          <Button variant="primary" onClick={onClose}>
            Done
          </Button>
        ) : driver ? (
          <>
            {drivers.length > 1 && (
              <Button variant="ghost" onClick={() => setDriver(null)}>
                Back
              </Button>
            )}
            <Button variant="primary" disabled={add.isPending} onClick={save}>
              {add.isPending ? "Adding…" : "Add provider"}
            </Button>
          </>
        ) : undefined
      }
    >
      {saved ? (
        <Banner tone="error" title="It’s saved, but its check failed.">
          {sentence(String(checkOf(saved)?.error ?? ""))} Fix it at the provider and use Check again in the list, or
          remove it and add it again.
        </Banner>
      ) : !driver ? (
        <ul aria-label="Kinds of provider" className="flex flex-col gap-2">
          {drivers.map((d) => (
            <li key={d.id}>
              <button
                type="button"
                onClick={() => pick(d)}
                className="w-full rounded-md border border-border px-3.5 py-2.5 text-left hover:bg-surface-neutral"
              >
                <span className="block text-[14px] font-bold text-fg">{d.label}</span>
                <span className="block text-[13px] text-fg-secondary">{d.about}</span>
                {d.priced && (
                  <span className="block text-[12.5px] text-fg-muted">Charges money: its groups need a budget.</span>
                )}
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <div className="flex flex-col gap-3">
          <Field label="Name" error={errors.name}>
            {({ id, describedBy, invalid }) => (
              <Input
                ref={nameRef}
                id={id}
                aria-describedby={describedBy}
                invalid={invalid}
                value={name}
                maxLength={80}
                onChange={(e) => setName(e.target.value)}
              />
            )}
          </Field>
          <SpecFields fields={driver.fields} form={form} onChange={setForm} errors={errors} />
          {note && <p className="text-[12.5px] leading-snug text-fg-muted">{note}</p>}
          {add.error && (
            <Banner tone="error" title="Couldn’t add the provider.">
              {add.error.message}
            </Banner>
          )}
        </div>
      )}
    </Dialog>
  );
}

/** Make a group on a provider: its name, bounds, budget (on a provider that charges) and what each unit is. Made
 * here, it comes back approved: making it is the person's pick. */
export function NewGroup({
  provider,
  driver,
  onClose,
}: {
  provider: Provider;
  driver: DriverSpec | undefined;
  onClose: () => void;
}) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const template = driver?.template ?? {};
  const priced = driver?.priced ?? provider.priced ?? true;
  const [name, setName] = useState(`${provider.name} workers`);
  const [min, setMin] = useState("0");
  const [max, setMax] = useState("1");
  const [budget, setBudget] = useState("");
  const [form, setForm] = useState<SpecForm>(() => specForm(template));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const make = useMutation({
    mutationFn: (body: {
      name: string;
      provider: number;
      min: number;
      max: number;
      template: Record<string, unknown>;
      budget?: number;
    }) => data(Scaling.createGroup({ client, body })),
    onSuccess: (g) => {
      void qc.invalidateQueries({ queryKey: ["scaling-groups"] });
      toast({ title: `Made ${g.name}`, body: termsText(g, [provider]), tone: "green" });
      onClose();
    },
  });
  const count = (s: string) => (/^\d+$/.test(s.trim()) && Number(s) <= 100 ? Number(s) : null);
  const save = () => {
    const { values, errors: bad } = specValues(template, form);
    const lo = count(min);
    const hi = count(max);
    const usd = Number(budget);
    if (!name.trim()) bad.name = "A name is needed.";
    if (lo === null) bad.min = "A whole number from 0 to 100.";
    if (hi === null) bad.max = "A whole number from 0 to 100.";
    else if (lo !== null && lo > hi) bad.max = "At least the minimum.";
    if (priced && !(budget.trim() && usd > 0)) bad.budget = "A budget is needed: this provider charges money.";
    setErrors(bad);
    if (Object.keys(bad).length || lo === null || hi === null) return;
    make.mutate({
      name: name.trim(),
      provider: provider.id,
      min: lo,
      max: hi,
      template: values,
      ...(priced ? { budget: usd } : {}),
    });
  };
  const num = (label: string, key: string, value: string, set: (v: string) => void, hint: string) => (
    <Field label={label} hint={hint} error={errors[key]}>
      {({ id, describedBy, invalid }) => (
        <Input
          id={id}
          aria-describedby={describedBy}
          invalid={invalid}
          type="number"
          min={0}
          value={value}
          onChange={(e) => set(e.target.value)}
        />
      )}
    </Field>
  );
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`New group on ${provider.name}`}
      description="Made here, it’s approved as it’s made: it may launch up to its maximum, within its budget."
      actions={
        <Button variant="primary" disabled={make.isPending} onClick={save}>
          {make.isPending ? "Making…" : "Make group"}
        </Button>
      }
    >
      <div className="flex flex-col gap-3">
        <Field label="Name" error={errors.name}>
          {({ id, describedBy, invalid }) => (
            <Input
              id={id}
              aria-describedby={describedBy}
              invalid={invalid}
              value={name}
              maxLength={80}
              onChange={(e) => setName(e.target.value)}
            />
          )}
        </Field>
        <div className="grid gap-3 sm:grid-cols-2">
          {num("Minimum", "min", min, setMin, "Units kept running.")}
          {num("Maximum", "max", max, setMax, "Units at most.")}
        </div>
        {priced && num("Budget, USD a month", "budget", budget, setBudget, "Its units stop at this cap.")}
        <SpecFields fields={template} form={form} onChange={setForm} errors={errors} />
        <p className="text-[12.5px] leading-[1.45] text-fg-secondary">
          It keeps its minimum running. Rules that add units when work waits and let them go when it doesn’t are set by
          the assistant, MCP or the API for now.
        </p>
        {make.error && (
          <Banner tone="error" title="Couldn’t make the group.">
            {make.error.message}
          </Banner>
        )}
      </div>
    </Dialog>
  );
}
