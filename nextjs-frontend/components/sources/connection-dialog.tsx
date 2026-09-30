"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";

import { Sources } from "@/app/openapi-client";
import type { Source, SourceHealth } from "@/app/openapi-client/types.gen";
import { ConnectionFields } from "@/components/sources/connection-fields";
import { TYPE_ICON } from "@/components/sources/source-icons";
import {
  TYPE_NAME,
  TYPE_ORDER,
  buildPayload,
  emptyForm,
  suggestName,
  validateForm,
  type BackendSpec,
  type ConnForm,
  type SourceType,
} from "@/components/sources/source-model";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { cn } from "@/lib/utils";

type Step = "type" | "details" | "test" | "name";
const STEPS: { key: Step; label: string }[] = [
  { key: "type", label: "Choose type" },
  { key: "details", label: "Details" },
  { key: "test", label: "Test" },
  { key: "name", label: "Name" },
];

function StepTrail({ step, editing }: { step: Step; editing: boolean }) {
  const steps = editing ? STEPS.filter((s) => s.key === "details" || s.key === "test") : STEPS;
  return (
    <ol aria-label="Steps" className="-mt-2 flex flex-wrap items-center gap-1.5 text-[12.5px] text-fg-muted">
      {steps.map((s, i) => (
        <li key={s.key} className="flex items-center gap-1.5">
          {i > 0 && <span aria-hidden>→</span>}
          <span
            aria-current={s.key === step ? "step" : undefined}
            className={cn(s.key === step && "font-bold text-fg-accent")}
          >
            {s.label}
          </span>
        </li>
      ))}
    </ol>
  );
}

/** SO2: add a connection (choose type → details → test → name), or edit one (details → test). */
export function ConnectionDialog({
  open,
  onOpenChange,
  backends,
  source,
  replaceToken,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  backends: Record<string, BackendSpec>;
  source?: Source | null;
  replaceToken?: boolean;
  onSaved?: (id: number) => void;
}) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const editing = Boolean(source);
  const [step, setStep] = useState<Step>(editing ? "details" : "type");
  const [type, setType] = useState<SourceType>(source?.type ?? "s3");
  const [form, setForm] = useState<ConnForm>(() =>
    emptyForm(backends[source?.type ?? "s3"] ?? { label: "", fields: {}, secrets: [] }, source),
  );
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [createdId, setCreatedId] = useState<number | null>(source?.id ?? null);
  const [result, setResult] = useState<{
    health: SourceHealth;
    ms: number;
  } | null>(null);
  /** Secrets saved during this dialog (the source prop doesn't know about them yet). */
  const [savedKeys, setSavedKeys] = useState<Set<string>>(new Set());
  const spec = backends[type];
  const saved = editing || createdId != null;
  const isSet = (k: string) => (Boolean(source?.secrets?.[k]?.set) && !savedKeys.has(`-${k}`)) || savedKeys.has(k);

  // Start fresh each time the dialog opens.
  useEffect(() => {
    if (!open) return;
    setStep(editing ? "details" : "type");
    setType(source?.type ?? "s3");
    setForm(
      emptyForm(
        backends[source?.type ?? "s3"] ?? {
          label: "",
          fields: {},
          secrets: [],
        },
        source,
      ),
    );
    setErrors({});
    setCreatedId(source?.id ?? null);
    setResult(null);
    setSavedKeys(new Set());
  }, [open]);

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["sources"] });
    void qc.invalidateQueries({ queryKey: ["watches"] });
  };

  const test = useMutation({
    mutationFn: async (mode: "save" | "retest") => {
      const t0 = performance.now();
      let id = createdId;
      let health: SourceHealth;
      let sent: Record<string, string | null> = {};
      if (mode === "retest" && id != null) health = await data(Sources.testSource({ client, path: { sid: id } }));
      else {
        const body = buildPayload(spec, form, type);
        sent = body.secrets;
        if (id == null) {
          const r = await data(
            Sources.createSource({
              client,
              body: { type, name: suggestName(type, form.params), ...body },
            }),
          );
          id = r.id;
          health = r.health;
        } else {
          const r = await data(
            Sources.updateSource({
              client,
              path: { sid: id },
              body: {
                ...body,
                ...(editing && form.name.trim() ? { name: form.name.trim() } : {}),
              },
            }),
          );
          health = r.health;
        }
      }
      return { id, health, ms: Math.round(performance.now() - t0), sent };
    },
    onSuccess: ({ id, health, ms, sent }) => {
      setCreatedId(id);
      setResult({ health, ms });
      // What was just saved counts as set (or cleared) from now on, so going back to the details doesn't ask again.
      setSavedKeys((prev) => {
        const next = new Set(prev);
        for (const [k, v] of Object.entries(sent)) {
          next.delete(v == null ? k : `-${k}`);
          next.add(v == null ? `-${k}` : k);
        }
        return next;
      });
      setForm((f) => ({ ...f, secrets: {}, tokenText: "" }));
      if (!form.name) setForm((f) => ({ ...f, name: suggestName(type, f.params) }));
      refresh();
    },
    onError: (e: Error) => {
      setStep("details");
      setErrors({ _: e.message });
    },
  });

  const rename = useMutation({
    mutationFn: (name: string) =>
      data(
        Sources.updateSource({
          client,
          path: { sid: createdId! },
          body: { name },
        }),
      ),
    onSuccess: () => {
      refresh();
      toast({ tone: "green", title: "Connection added", body: form.name });
      onSaved?.(createdId!);
      onOpenChange(false);
    },
    onError: (e: Error) => setErrors({ name: e.message }),
  });

  const discard = useMutation({
    mutationFn: () => data(Sources.deleteSource({ client, path: { sid: createdId! } })),
    onSuccess: () => {
      refresh();
      onOpenChange(false);
    },
  });

  const goTest = () => {
    const e = validateForm(type, spec, form, saved, isSet);
    setErrors(e);
    if (Object.keys(e).length) return;
    setResult(null);
    setStep("test");
    test.mutate("save");
  };

  const title =
    step === "type"
      ? "Add a connection"
      : step === "details"
        ? editing
          ? `Edit “${source?.name}”`
          : TYPE_NAME[type] === "S3 / compatible"
            ? "S3 or S3-compatible"
            : spec?.oauth
              ? `${TYPE_NAME[type]} · paste a token`
              : (spec?.label ?? TYPE_NAME[type])
        : step === "test"
          ? test.isPending
            ? "Testing…"
            : result?.health.ok
              ? "Test · passed"
              : "Test · failed"
          : "Name the connection";

  let body: ReactNode = null;
  let actions: ReactNode = null;
  if (step === "type") {
    const order = TYPE_ORDER.filter((t) => backends[t]);
    // Arrow keys move the choice, as in any radio group.
    const onArrow = (e: React.KeyboardEvent<HTMLDivElement>) => {
      const d = { ArrowRight: 1, ArrowDown: 2, ArrowLeft: -1, ArrowUp: -2 }[e.key];
      if (!d) return;
      e.preventDefault();
      const next = order[(order.indexOf(type) + d + order.length) % order.length];
      setType(next);
      (e.currentTarget.querySelector(`[data-type="${next}"]`) as HTMLElement | null)?.focus();
    };
    body = (
      <div role="radiogroup" aria-label="Type" className="grid grid-cols-2 gap-2" onKeyDown={onArrow}>
        {order.map((t) => {
          const Icon = TYPE_ICON[t];
          const on = t === type;
          return (
            <button
              key={t}
              type="button"
              role="radio"
              data-type={t}
              aria-checked={on}
              tabIndex={on ? 0 : -1}
              onClick={() => setType(t)}
              onDoubleClick={() => {
                setType(t);
                setForm(emptyForm(backends[t]));
                setStep("details");
              }}
              className={cn(
                "flex h-11 items-center gap-2 rounded-[10px] px-3 text-left text-[13px] font-semibold text-fg transition-colors duration-fast",
                on ? "border-2 border-blue bg-blue-surface" : "border border-border bg-background hover:bg-surface",
              )}
            >
              <Icon aria-hidden className="size-[17px] text-fg-secondary" />
              {TYPE_NAME[t]}
            </button>
          );
        })}
      </div>
    );
    actions = (
      <>
        <Button variant="ghost" onClick={() => onOpenChange(false)}>
          Cancel
        </Button>
        <Button
          variant="primary"
          onClick={() => {
            setForm(emptyForm(spec));
            setErrors({});
            setStep("details");
          }}
        >
          Next
        </Button>
      </>
    );
  } else if (step === "details") {
    body = (
      <div className="flex flex-col gap-3">
        {editing && (
          <Field label="Name" error={errors.name}>
            {({ id }) => (
              <Input id={id} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            )}
          </Field>
        )}
        {spec ? (
          <ConnectionFields
            type={type}
            spec={spec}
            form={form}
            onChange={setForm}
            errors={errors}
            saved={saved}
            isSet={isSet}
            replaceToken={replaceToken}
          />
        ) : null}
        {errors._ && (
          <p
            role="alert"
            className="rounded-sm border border-red-border bg-red-surface px-3 py-2 text-[13px] text-red-dark"
          >
            {errors._}
          </p>
        )}
      </div>
    );
    actions = (
      <>
        {!editing && (
          <Button variant="ghost" className="mr-auto" onClick={() => setStep("type")}>
            Back
          </Button>
        )}
        <Button variant="ghost" onClick={() => onOpenChange(false)}>
          Cancel
        </Button>
        <Button variant="primary" onClick={goTest}>
          {editing ? "Save and test" : "Test connection"}
        </Button>
      </>
    );
  } else if (step === "test") {
    const ok = result?.health.ok;
    body = (
      <div className="flex flex-col gap-3" aria-live="polite">
        <div className="grid grid-cols-[20px_1fr_auto] items-center gap-2.5 text-[13.5px] font-medium">
          <span
            aria-hidden
            className={cn(
              "grid size-5 place-items-center rounded-full text-[10px] font-extrabold text-white",
              test.isPending ? "animate-pulse bg-blue" : ok ? "bg-green" : "bg-red",
            )}
          >
            {test.isPending ? "" : ok ? "✓" : "✕"}
          </span>
          <span>
            {test.isPending
              ? "Reaching the storage and listing its top folder…"
              : ok
                ? "Listed the top folder"
                : "Couldn’t list the top folder"}
          </span>
          <span className="tabular text-[12px] text-fg-muted">{result ? `${result.ms} ms` : ""}</span>
        </div>
        {result && !ok && (
          <div className="flex gap-2 rounded-[10px] border border-red-border bg-red-surface px-3 py-2.5 text-[13px] leading-normal">
            <span aria-hidden className="font-extrabold text-red">
              ✕
            </span>
            <code className="break-words font-mono text-[12.5px] text-fg">
              {result.health.error ?? "The test failed"}
            </code>
          </div>
        )}
        {result && ok && (
          <p className="text-[13px] text-fg-secondary">
            The archive can read this storage. {editing ? "Changes are saved." : "Give it a name you’ll recognise."}
          </p>
        )}
        {result && !ok && (
          <p className="text-[12.5px] text-fg-secondary">
            The text above is the storage’s own error.{" "}
            {editing
              ? "Your changes are saved."
              : "The connection is saved; you can fix the details, test again, or discard it."}
          </p>
        )}
      </div>
    );
    actions = test.isPending ? (
      <Button size="sm" variant="ghost" disabled>
        Testing…
      </Button>
    ) : editing ? (
      <>
        <Button size="sm" variant="ghost" onClick={() => setStep("details")}>
          Edit details
        </Button>
        {!result?.health.ok && (
          <Button size="sm" variant="secondary" onClick={() => test.mutate("retest")}>
            Test again
          </Button>
        )}
        <Button size="sm" variant="primary" onClick={() => onOpenChange(false)}>
          Done
        </Button>
      </>
    ) : (
      <>
        {!result?.health.ok && (
          <Button
            size="sm"
            variant="danger-ghost"
            className="mr-auto"
            onClick={() => discard.mutate()}
            disabled={discard.isPending}
          >
            Discard
          </Button>
        )}
        <Button size="sm" variant="ghost" onClick={() => setStep("details")}>
          Edit details
        </Button>
        {result?.health.ok ? (
          <Button size="sm" variant="primary" onClick={() => setStep("name")}>
            Next
          </Button>
        ) : (
          <>
            <Button size="sm" variant="secondary" onClick={() => setStep("name")}>
              Keep anyway
            </Button>
            <Button size="sm" variant="primary" onClick={() => test.mutate("retest")}>
              Test again
            </Button>
          </>
        )}
      </>
    );
  } else {
    body = (
      <Field
        label="Name"
        hint="Shown in the list, in watched folders and on imported recordings’ paths"
        error={errors.name}
      >
        {({ id, describedBy, invalid }) => (
          <Input
            id={id}
            aria-describedby={describedBy}
            invalid={invalid}
            autoFocus
            value={form.name}
            maxLength={80}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            onKeyDown={(e) => e.key === "Enter" && form.name.trim() && rename.mutate(form.name.trim())}
          />
        )}
      </Field>
    );
    actions = (
      <>
        <Button variant="ghost" onClick={() => setStep("test")}>
          Back
        </Button>
        <Button
          variant="primary"
          disabled={!form.name.trim() || rename.isPending}
          onClick={() => rename.mutate(form.name.trim())}
        >
          {rename.isPending ? "Saving…" : "Save connection"}
        </Button>
      </>
    );
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o && !editing && createdId != null && step !== "name") refresh();
        onOpenChange(o);
      }}
      title={title}
      className="max-w-[520px]"
      actions={actions}
    >
      <StepTrail step={step} editing={editing} />
      {body}
    </Dialog>
  );
}
