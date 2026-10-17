"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { Sources } from "@/app/openapi-client";
import type { Source, SourceHealth } from "@/app/openapi-client/types.gen";
import { ConnectionFields } from "@/components/sources/connection-fields";
import { startRclone, type RcloneWaiting } from "@/components/sources/rclone-signin";
import { RcloneWait } from "@/components/sources/rclone-wait";
import { TYPE_ICON } from "@/components/sources/source-icons";
import {
  SIGNIN_SERVICE,
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
import { navigate } from "@/lib/navigate";
import { cn } from "@/lib/utils";

type Step = "type" | "details" | "test";
const STEPS: { key: Step; label: string }[] = [
  { key: "type", label: "Choose type" },
  { key: "details", label: "Details" },
  { key: "test", label: "Test and name" },
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

/** SO2: add a connection (choose type → details → test, named there), or edit one (details → test). A type with
 * nothing to fill in (a folder on this machine) is added, tested and named at once: the test step only shows if it
 * fails. Dropbox, OneDrive and Google Drive (as a Google account) sign in at the service in a new tab, through rclone
 * on the server (RcloneWait), instead of testing a pasted token: the connection is made and tested when the service
 * sends the browser back, and the dialog goes on to the test step. */
export function ConnectionDialog({
  open,
  onOpenChange,
  backends,
  source,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  backends: Record<string, BackendSpec>;
  source?: Source | null;
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
  /** The name the connection was created with (suggested from its details), so an unchanged name isn't saved again. */
  const [createdName, setCreatedName] = useState("");
  const [result, setResult] = useState<{
    health: SourceHealth;
    /** How long the test took; null when the connection was tested as it was made (signing in). */
    ms: number | null;
  } | null>(null);
  /** Waiting for the browser to come back to rclone on the server; `rcloneStarting` while it's asked to. */
  const [rclone, setRclone] = useState<RcloneWaiting | null>(null);
  const [rcloneStarting, setRcloneStarting] = useState(false);
  const rcloneLive = useRef<RcloneWaiting | null>(null);
  const waitRclone = (w: RcloneWaiting | null) => {
    rcloneLive.current = w;
    setRclone(w);
  };
  /** Stop waiting for rclone (cancelling, closing, starting over): it stops on the server too. */
  const dropRclone = () => {
    const w = rcloneLive.current;
    if (w) void Sources.cancelRcloneSignin({ client, path: { fid: w.id } });
    waitRclone(null);
  };
  /** Secrets saved during this dialog (the source prop doesn't know about them yet). */
  const [savedKeys, setSavedKeys] = useState<Set<string>>(new Set());
  const spec = backends[type];
  const saved = editing || createdId != null;
  const isSet = (k: string) => (Boolean(source?.secrets?.[k]?.set) && !savedKeys.has(`-${k}`)) || savedKeys.has(k);

  // Start fresh each time the dialog opens; a sign-in through rclone stops when it closes.
  useEffect(() => {
    dropRclone();
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
  useEffect(() => () => dropRclone(), []);

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["sources"] });
    void qc.invalidateQueries({ queryKey: ["watches"] });
  };

  const test = useMutation({
    // `type` and `form` are passed when the type was only just picked (this render's are the previous ones)
    mutationFn: async ({
      mode,
      type: t = type,
      form: f = form,
    }: {
      mode: "save" | "retest";
      type?: SourceType;
      form?: ConnForm;
      finish?: boolean;
    }) => {
      const t0 = performance.now();
      let id = createdId;
      let health: SourceHealth;
      let sent: Record<string, string | null> = {};
      let name = "";
      if (mode === "retest" && id != null) health = await data(Sources.testSource({ client, path: { sid: id } }));
      else {
        const body = buildPayload(backends[t], f, t);
        sent = body.secrets;
        if (id == null) {
          name = f.name.trim() || suggestName(t, f.params);
          const r = await data(
            Sources.createSource({
              client,
              body: { type: t, name, ...body },
            }),
          );
          setCreatedName(name);
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
      return { id, health, ms: Math.round(performance.now() - t0), sent, name };
    },
    onSuccess: ({ id, health, ms, sent, name }, { finish }) => {
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
      setForm((f) => ({ ...f, secrets: {}, tokenText: "", name: f.name || name }));
      refresh();
      if (finish && health.ok && id != null) {
        toast({ tone: "green", title: "Connection added", body: name });
        onSaved?.(id);
        onOpenChange(false);
      }
    },
    onError: (e: Error) => {
      setStep("details");
      setErrors({ _: e.message });
    },
  });

  const rename = useMutation({
    mutationFn: async (name: string) => {
      if (name !== createdName) await data(Sources.updateSource({ client, path: { sid: createdId! }, body: { name } }));
    },
    onSuccess: () => {
      refresh();
      toast({ tone: "green", title: "Connection added", body: form.name.trim() || createdName });
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

  // Connect with Google: the connection is saved as it is (made first when new), then this browser goes to Google's
  // consent screen and comes back to Sources with the account connected
  const connectGoogle = useMutation({
    mutationFn: async (oauthClient: { client_id?: string; client_secret?: string }) => {
      let id = createdId;
      const body = buildPayload(backends[type], form, type);
      if (id == null) {
        const name = form.name.trim() || suggestName(type, form.params);
        id = (await data(Sources.createSource({ client, body: { type, name, ...body } }))).id;
        setCreatedId(id);
        setCreatedName(name);
      } else
        await data(
          Sources.updateSource({
            client,
            path: { sid: id },
            body: { ...body, ...(editing && form.name.trim() ? { name: form.name.trim() } : {}) },
          }),
        );
      return (await data(Sources.connectGoogle({ client, path: { sid: id }, body: oauthClient }))).url;
    },
    onSuccess: (url) => navigate.assign(url),
    onError: (e: Error) => {
      refresh();
      setErrors({ _: e.message });
    },
  });

  const goConnect = (oauthClient: { client_id?: string; client_secret?: string }) => {
    const e = validateForm(type, spec, form, saved, isSet);
    delete e.token; // what Google's consent screen is for
    setErrors(e);
    if (!Object.keys(e).length) connectGoogle.mutate(oauthClient);
  };

  // Dropbox, OneDrive and Google Drive (as a Google account) sign in at the service through rclone on the server,
  // unless a token is pasted or a saved connection only changes its options
  const via = spec?.oauth && !(type === "drive" && form.driveAuth === "service_account") ? form.signInWith : null;
  const needSignIn = via === "rclone" && !(saved && isSet("token"));
  const service = SIGNIN_SERVICE[type] ?? spec?.label ?? "";

  // the connection is made and tested: on to the test step, as if it had been saved here
  const connected = async (id: number) => {
    const made = (await data(Sources.listSources({ client }))).find((s) => s.id === id);
    setCreatedId(id);
    setCreatedName(made?.name ?? "");
    setSavedKeys((prev) => new Set([...prev, "token", "-service_account_credentials"]));
    setForm((f) => ({ ...f, secrets: {}, tokenText: "", name: editing ? f.name : (made?.name ?? f.name) }));
    setResult({ health: made?.health ?? { ok: false, error: "Not tested yet" }, ms: null });
    setStep("test");
    refresh();
  };

  // the service's page opens in a new tab now, in the click; the dialog then waits for the address it ends on
  const startSignIn = async () => {
    const e = validateForm(type, spec, form, saved, isSet);
    setErrors(e);
    if (Object.keys(e).length) return;
    setRcloneStarting(true);
    try {
      const { params } = buildPayload(spec, form, type);
      waitRclone(
        await startRclone(
          client,
          createdId != null ? { source: createdId, params } : { type, params, name: form.name.trim() || null },
        ),
      );
    } catch (err) {
      setErrors({ _: err instanceof Error ? err.message : String(err) });
    } finally {
      setRcloneStarting(false);
    }
  };

  const goTest = () => {
    const e = validateForm(type, spec, form, saved, isSet);
    setErrors(e);
    if (Object.keys(e).length) return;
    setResult(null);
    setStep("test");
    test.mutate({ mode: "save" });
  };

  const title =
    step === "type"
      ? "Add a connection"
      : step === "details"
        ? editing
          ? `Edit “${source?.name}”`
          : TYPE_NAME[type] === "S3 / compatible"
            ? "S3 or S3-compatible"
            : (spec?.label ?? TYPE_NAME[type])
        : step === "test"
          ? test.isPending
            ? "Testing…"
            : result?.health.ok
              ? "Test · passed"
              : "Test · failed"
          : "";

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
              onClick={() => {
                const blank = emptyForm(backends[t]);
                setType(t);
                setForm(blank);
                setErrors({});
                if (Object.keys(backends[t].fields).length || backends[t].secrets.length) setStep("details");
                else {
                  setResult(null);
                  setStep("test");
                  test.mutate({ mode: "save", type: t, form: blank, finish: true });
                }
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
      <Button variant="ghost" onClick={() => onOpenChange(false)}>
        Cancel
      </Button>
    );
  } else if (step === "details") {
    const alert = errors._ && (
      <p
        role="alert"
        className="rounded-sm border border-red-border bg-red-surface px-3 py-2 text-[13px] text-red-dark"
      >
        {errors._}
      </p>
    );
    body = rclone ? (
      <div className="flex flex-col gap-3">
        <RcloneWait
          waiting={rclone}
          service={service}
          onConnected={(id) => {
            waitRclone(null);
            void connected(id).catch((e: Error) => setErrors({ _: e.message }));
          }}
          onOver={(why) => {
            waitRclone(null);
            setErrors({ _: why });
          }}
        />
        {alert}
      </div>
    ) : (
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
            onSignIn={() => void startSignIn()}
            signingIn={rcloneStarting}
            onConnectGoogle={goConnect}
            connectingGoogle={connectGoogle.isPending || connectGoogle.isSuccess}
          />
        ) : null}
        {alert}
      </div>
    );
    actions = rclone ? (
      <>
        <Button variant="ghost" onClick={dropRclone}>
          Cancel
        </Button>
        <Button variant="primary" disabled disabledReason={`Finish signing in to ${service} in the new tab`}>
          {`Waiting for ${service}…`}
        </Button>
      </>
    ) : (
      <>
        {!editing && (
          <Button variant="ghost" className="mr-auto" onClick={() => setStep("type")}>
            Back
          </Button>
        )}
        <Button variant="ghost" onClick={() => onOpenChange(false)}>
          Cancel
        </Button>
        {needSignIn ? (
          <Button
            variant="primary"
            onClick={() => void startSignIn()}
            disabled={rcloneStarting}
            disabledReason={`Opening ${service}’s sign-in page`}
          >
            {rcloneStarting ? `Waiting for ${service}…` : `Sign in with ${service}`}
          </Button>
        ) : (
          <Button variant="primary" onClick={goTest} disabled={rcloneStarting}>
            {editing ? "Save and test" : "Test connection"}
          </Button>
        )}
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
          <span className="tabular text-[12px] text-fg-muted">{result?.ms != null ? `${result.ms} ms` : ""}</span>
        </div>
        {result && !ok && (
          <div className="flex gap-2 rounded-[10px] border border-red-border bg-red-surface px-3 py-2.5 text-[13px] leading-normal">
            <span aria-hidden className="font-extrabold text-red">
              ✕
            </span>
            <code className="wrap-break-word font-mono text-[12.5px] text-fg">
              {result.health.error ?? "The test failed"}
            </code>
          </div>
        )}
        {result && ok && (
          <p className="text-[13px] text-fg-secondary">
            The archive can read this storage. {editing ? "Changes are saved." : "Next, pick a folder to watch."}
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
        {result && !editing && (
          <Field
            label="Name"
            hint="Shown in the list, in watched folders and on imported assets’ paths"
            error={errors.name}
          >
            {({ id, describedBy, invalid }) => (
              <Input
                id={id}
                aria-describedby={describedBy}
                invalid={invalid}
                value={form.name}
                maxLength={80}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            )}
          </Field>
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
          <Button size="sm" variant="secondary" onClick={() => test.mutate({ mode: "retest" })}>
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
          <Button
            size="sm"
            variant="primary"
            disabled={!form.name.trim() || rename.isPending}
            onClick={() => rename.mutate(form.name.trim())}
          >
            {rename.isPending ? "Saving…" : "Done"}
          </Button>
        ) : (
          <>
            <Button
              size="sm"
              variant="secondary"
              disabled={!form.name.trim() || rename.isPending}
              onClick={() => rename.mutate(form.name.trim())}
            >
              Keep anyway
            </Button>
            <Button size="sm" variant="primary" onClick={() => test.mutate({ mode: "retest" })}>
              Test again
            </Button>
          </>
        )}
      </>
    );
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o && !editing && createdId != null) refresh();
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
