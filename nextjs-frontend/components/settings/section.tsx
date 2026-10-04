"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { Admin, Metadata } from "@/app/openapi-client";
import { SectionBody, type BodyCtx, type NsAccess } from "@/components/settings/bodies";
import type { FieldState } from "@/components/settings/fields";
import {
  buildPatches,
  crossErrors,
  fieldId,
  fieldsOf,
  getPath,
  parse,
  same,
  SECTIONS,
  serverError,
  show,
  toUi,
  why,
  type Change,
  type SectionId,
  type SettingsView,
} from "@/components/settings/model";
import { useUnsavedGuard } from "@/components/settings/use-unsaved-guard";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { absolute, count } from "@/lib/format";
import { useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

/** Env variables that override a setting (the backend reports the key as locked). */
const ENV: Record<string, string> = {
  "server.allowed_hosts": "ARCHIVE_ALLOWED_HOSTS",
  "llm.base_url": "LENS_LLM_BASE_URL",
  "llm.model": "LENS_LLM_MODEL",
  "llm.api_key": "LENS_LLM_API_KEY",
  "llm.vision_model": "LENS_LLM_VISION_MODEL",
  "telemetry.enabled": "LENS_TELEMETRY",
  "telemetry.endpoint": "LENS_TELEMETRY_ENDPOINT",
  "telemetry.headers": "LENS_TELEMETRY_HEADERS",
  "fedora.enabled": "LENS_FEDORA",
  "fedora.url": "LENS_FEDORA_URL",
  "fedora.user": "LENS_FEDORA_USER",
  "fedora.password": "LENS_FEDORA_PASSWORD",
};
const CONFIRM_BASE = "CHANGE ALL IDENTIFIERS";

/** One settings section (ST1–ST3, MD5): fields, inline checks, who saved it last, review & save. */
export function SettingsSection({
  id,
  view,
  onErrors,
}: {
  id: SectionId;
  view: SettingsView;
  onErrors: (id: SectionId, has: boolean) => void;
}) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const router = useRouter();
  const { namespaces } = useArchive();
  const spec = SECTIONS.find((s) => s.id === id)!;
  const specs = useMemo(() => fieldsOf(id), [id]);
  const initial = useMemo(
    () => Object.fromEntries(specs.map((f) => [fieldId(f), toUi(f, getPath(view[f.section]?.values, f.key))])),
    [specs, view],
  );
  const [form, setForm] = useState<Record<string, unknown>>(initial);
  const [serverErrors, setServerErrors] = useState<Record<string, string>>({});
  const [banner, setBanner] = useState<string | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [typed, setTyped] = useState("");
  const [nsAccess, setNsAccess] = useState<NsAccess>({});
  const [nsInitial, setNsInitial] = useState<NsAccess>({});
  const lastView = useRef(view);

  const parsed = useMemo(
    () => Object.fromEntries(specs.map((f) => [fieldId(f), parse(f, form[fieldId(f)])])),
    [specs, form],
  );
  const values = useMemo(
    () => Object.fromEntries(Object.entries(parsed).map(([k, p]) => [k, "value" in p ? p.value : undefined])),
    [parsed],
  );
  const original = useMemo(
    () => Object.fromEntries(specs.map((f) => [fieldId(f), getPath(view[f.section]?.values, f.key)])),
    [specs, view],
  );

  const changes: Change[] = specs
    .filter((f) => {
      const k = fieldId(f);
      if (f.kind === "secret") return form[k] !== undefined;
      return "value" in parsed[k] && !same(values[k], original[k]);
    })
    .map((f) => ({
      id: fieldId(f),
      field: f,
      before: original[fieldId(f)],
      after: values[fieldId(f)],
    }));
  const nsChanges = Object.keys(nsAccess).filter((n) => nsAccess[n] !== nsInitial[n]);
  const dirty = changes.length + nsChanges.length > 0;

  const errors: Record<string, string> = {
    ...crossErrors(values),
    ...serverErrors,
  };
  for (const [k, p] of Object.entries(parsed)) if ("error" in p) errors[k] = p.error;
  const errorCount = Object.keys(errors).length;

  useEffect(() => onErrors(id, errorCount > 0), [id, errorCount, onErrors]);
  // A fresh copy from the server replaces the form unless you're in the middle of changing it.
  useEffect(() => {
    if (lastView.current === view) return;
    lastView.current = view;
    if (!dirty) setForm(initial);
  }, [view, initial, dirty]);

  const [held, setHeld] = useUnsavedGuard(dirty);

  const locked = (f: (typeof specs)[number]) =>
    (view[f.section]?.locked ?? []).includes(f.key.split(".")[0]) ? (ENV[fieldId(f)] ?? "the environment") : null;
  const ctx: BodyCtx = {
    section: id,
    view,
    form,
    values,
    spec: (k) => specs.find((f) => fieldId(f) === k)!,
    state: (k): FieldState => {
      const f = specs.find((x) => fieldId(x) === k)!;
      return {
        value: form[k],
        error: errors[k],
        locked: locked(f),
        onChange: (v) => {
          setForm((s) => ({ ...s, [k]: v }));
          setServerErrors((e) => {
            if (!e[k]) return e;
            const n = { ...e };
            delete n[k];
            return n;
          });
        },
      };
    },
    nsAccess,
    setNsAccess: (n, v) => setNsAccess((s) => ({ ...s, [n]: v })),
    initNsAccess: (m) => {
      setNsInitial(m);
      setNsAccess(m);
    },
    dirty,
  };

  const save = useMutation({
    mutationFn: async () => {
      const patches = buildPatches(changes, view);
      for (const [section, body] of Object.entries(patches)) {
        try {
          await data(Admin.updateSettings({ client, path: { section }, body }));
        } catch (e) {
          throw Object.assign(e instanceof Error ? e : new Error(String(e)), {
            section,
          });
        }
      }
      for (const n of nsChanges) {
        const current = (await data(Metadata.getNamespaceMetadata({ client, path: { name: n } }))) as unknown as {
          profile: Record<string, unknown>;
        };
        await data(
          Metadata.updateNamespaceMetadata({
            client,
            path: { name: n },
            body: {
              profile: { ...current.profile, default_access: nsAccess[n] },
            },
          }),
        );
      }
    },
    onSuccess: async () => {
      const n = changes.length + nsChanges.length;
      setReviewing(false);
      setTyped("");
      setBanner(null);
      setServerErrors({});
      setNsInitial(nsAccess);
      await qc.invalidateQueries({ queryKey: ["settings"] });
      if (nsChanges.length) void qc.invalidateQueries({ queryKey: ["namespace-metadata"] });
      toast({
        title: `Saved ${spec.label}`,
        body: `${n} change${n === 1 ? "" : "s"}, recorded in the audit log as settings.save.`,
        tone: "green",
      });
    },
    onError: (e: Error & { section?: string }) => {
      setReviewing(false);
      const mapped = serverError(e.section ?? "", e instanceof ApiError ? e.message : e.message);
      if (mapped.field)
        setServerErrors((s) => ({
          ...s,
          [mapped.field as string]: mapped.message,
        }));
      else setBanner(mapped.message);
    },
  });

  const discard = () => {
    setForm(initial);
    setNsAccess(nsInitial);
    setServerErrors({});
    setBanner(null);
  };

  const updatedAt = spec.backend
    .map((b) => view[b]?.updated_at)
    .filter(Boolean)
    .sort()
    .pop();
  const updatedBy = spec.backend.map((b) => view[b]).find((v) => v?.updated_at === updatedAt)?.updated_by;
  const baseChange = changes.find((c) => c.id === "iiif.base_url");
  const firstError = Object.keys(errors)[0];
  const errorLabel = firstError
    ? (specs.find((f) => fieldId(f) === firstError)?.label ?? "the marked field").toLowerCase()
    : "";
  const manifests = namespaces.reduce((a, n) => a + (n.recordings ?? 0), 0);

  // One click saves; only a new public base URL (every IIIF identifier changes) is reviewed and typed out first.
  const canSave = dirty && errorCount === 0 && !save.isPending;
  const commit = () => (baseChange ? setReviewing(true) : save.mutate());
  const commitRef = useRef(commit);
  commitRef.current = canSave ? commit : () => undefined;
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        commitRef.current();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return (
    <div className="relative flex min-h-full flex-col">
      <div className="flex max-w-[820px] flex-col gap-[18px] px-4 pb-24 pt-[22px] sm:px-8">
        <div className="flex flex-col gap-1.5">
          <h2 className="text-[22px] font-bold leading-[1.2] text-fg">{spec.label}</h2>
          <p className="text-[14px] leading-normal text-fg-secondary">{spec.description}</p>
          {id !== "startup" && (
            <span className="text-[12.5px] text-fg-muted">
              {updatedAt
                ? `Last saved by ${updatedBy ?? "someone"} on ${absolute(updatedAt)}`
                : "Not changed in the app yet: these are the server’s defaults and config file values."}
            </span>
          )}
        </div>
        {banner && (
          <Banner tone="error" onDismiss={() => setBanner(null)}>
            {banner}
          </Banner>
        )}
        <SectionBody ctx={ctx} />
      </div>

      {id !== "startup" && (dirty || errorCount > 0) && (
        <div className="sticky bottom-0 z-10 mt-auto flex flex-wrap items-center gap-2.5 border-t border-border bg-background px-4 py-3 sm:px-8">
          <span aria-hidden className={cn("size-2 rounded-full", errorCount ? "bg-red" : "bg-blue")} />
          <span className="min-w-0 flex-1 text-[13.5px] font-medium" aria-live="polite">
            {changes.length + nsChanges.length} change
            {changes.length + nsChanges.length === 1 ? "" : "s"}
            {errorCount
              ? ` · fix ${errorCount === 1 ? `the ${errorLabel}` : `${errorCount} fields`} before saving`
              : ""}
          </span>
          <Button variant="ghost" size="sm" onClick={discard} disabled={save.isPending}>
            Discard
          </Button>
          <Button
            variant="primary"
            size="sm"
            disabled={!dirty || errorCount > 0 || save.isPending}
            disabledReason={errorCount ? "Fix the fields marked in red first" : undefined}
            onClick={commit}
          >
            {save.isPending ? "Saving…" : baseChange ? "Review & save" : "Save"}
          </Button>
        </div>
      )}

      <Dialog
        open={reviewing}
        onOpenChange={(o) => !o && (setReviewing(false), setTyped(""))}
        title={
          baseChange && changes.length === 1 && !nsChanges.length
            ? "Change the public base URL?"
            : `Save ${spec.label}?`
        }
        description={
          <>
            These apply to everyone as soon as you save, and are recorded in the audit log as{" "}
            <code className="font-mono">settings.save</code>.
          </>
        }
        actions={
          <>
            <Button variant="ghost" onClick={() => setReviewing(false)}>
              Back
            </Button>
            <Button
              variant={baseChange ? "danger" : "primary"}
              disabled={save.isPending || (Boolean(baseChange) && typed.trim() !== CONFIRM_BASE)}
              disabledReason={
                baseChange && typed.trim() !== CONFIRM_BASE ? `Type ${CONFIRM_BASE} to confirm` : undefined
              }
              onClick={() => save.mutate()}
            >
              {save.isPending
                ? "Saving…"
                : baseChange
                  ? "Change base URL"
                  : `Save ${changes.length + nsChanges.length} change${changes.length + nsChanges.length === 1 ? "" : "s"}`}
            </Button>
          </>
        }
      >
        {baseChange && (
          <>
            <div className="flex gap-2.5 rounded-md border border-red-border bg-red-surface p-3 text-[13.5px] leading-normal">
              <span aria-hidden className="font-extrabold text-red">
                ✕
              </span>
              <span>
                Every IIIF identifier changes:{" "}
                <b>
                  {count(manifests)} Manifest{manifests === 1 ? "" : "s"} and {count(namespaces.length + 1)} Collections
                </b>
                . Links saved in other viewers, harvesters and citations stop resolving unless the old address keeps
                redirecting — set that up on your web server; Lens can’t redirect it itself.
              </span>
            </div>
            <div className="grid grid-cols-[48px_minmax(0,1fr)] gap-1.5 font-mono text-[12.5px] leading-normal">
              <span className="text-fg-muted">from</span>
              <span className="break-all text-red-dark line-through">
                {(baseChange.before as string) || "the address each request comes to"}
              </span>
              <span className="text-fg-muted">to</span>
              <b className="break-all">{(baseChange.after as string) || "the address each request comes to"}</b>
            </div>
            <label className="flex flex-col gap-1.5 text-[13px] font-bold text-fg-strong">
              Type {CONFIRM_BASE} to confirm
              <Input value={typed} onChange={(e) => setTyped(e.target.value)} mono autoComplete="off" />
            </label>
          </>
        )}
        {changes
          .filter((c) => c !== baseChange)
          .map((c) => (
            <ChangeRow
              key={c.id}
              label={c.field.label}
              before={show(c.field, c.before)}
              after={show(c.field, c.after)}
              note={why(c)}
            />
          ))}
        {nsChanges.map((n) => (
          <ChangeRow
            key={n}
            label={`Access · ${n}`}
            before={nsInitial[n]}
            after={nsAccess[n]}
            note="The default for recordings without their own access."
          />
        ))}
      </Dialog>

      <Dialog
        open={Boolean(held)}
        onOpenChange={(o) => !o && setHeld(null)}
        title="Leave with unsaved changes?"
        description={`You changed ${changes.length + nsChanges.length} setting${changes.length + nsChanges.length === 1 ? "" : "s"} in ${spec.label} and haven’t saved.`}
        actions={
          <>
            <Button variant="ghost" onClick={() => setHeld(null)}>
              Keep editing
            </Button>
            <Button
              variant="danger"
              onClick={() => {
                const to = held;
                discard();
                setHeld(null);
                if (to) setTimeout(() => router.push(to), 0);
              }}
            >
              Discard
            </Button>
          </>
        }
      />
    </div>
  );
}

function ChangeRow({
  label,
  before,
  after,
  note,
}: {
  label: string;
  before: string;
  after: string;
  note?: string | null;
}) {
  return (
    <div className="grid gap-2.5 rounded-[10px] border border-border bg-surface px-3 py-2.5 text-[13px] leading-[1.45] sm:grid-cols-[150px_minmax(0,1fr)]">
      <b>{label}</b>
      <span className="min-w-0 break-words">
        <span className="text-red-dark line-through">{before}</span> → <b>{after}</b>
        {note && <span className="block text-fg-muted">{note}</span>}
      </span>
    </div>
  );
}
