"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Lock } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";

import {
  Admin,
  Setup,
  Sources,
  type FileStoreTestResult,
  type LlmTestResult,
  type SetupView,
  type TelemetryTestResult,
} from "@/app/openapi-client";
import { mcpAddress } from "@/components/account/connect-assistant";
import { AuthAlert, AuthBrand } from "@/components/auth/auth-card";
import { SegmentedChoice } from "@/components/settings/controls";
import {
  KeyLink,
  ModelProviderPicker,
  providerAt,
  useModelProviders,
  type ModelProvider,
} from "@/components/settings/model-providers";
import { AddConnection } from "@/components/storage/add-connection";
import { NOT_STORAGE, fileStoreTry } from "@/components/storage/file-store";
import { Button } from "@/components/ui/button";
import { Field, Input, Select, Switch } from "@/components/ui/field";
import { Skeleton } from "@/components/ui/states";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

const STEPS = [
  { id: "admin", label: "Admin account" },
  { id: "namespace", label: "Namespace" },
  { id: "llm", label: "Model provider" },
  { id: "budget", label: "Budget" },
  { id: "storage", label: "Storage" },
  { id: "apps", label: "Apps and AI" },
  { id: "telemetry", label: "Telemetry" },
] as const;
type StepId = (typeof STEPS)[number]["id"];
/** Steps with a default: not asked, but a click away for whoever wants to choose (and all in Settings). */
const OPTIONAL: StepId[] = ["namespace", "storage", "apps", "telemetry"];

/** The .env variable that sets each locked field (fastapi_backend/app/domain/settings.py, ENV_OVERRIDES). */
const LLM_ENV: Record<string, string> = {
  base_url: "LENS_LLM_BASE_URL",
  model: "LENS_LLM_MODEL",
  api_key: "LENS_LLM_API_KEY",
};
const TELEMETRY_ENV: Record<string, string> = { enabled: "LENS_TELEMETRY", endpoint: "LENS_TELEMETRY_ENDPOINT" };
const NS = /^[a-z0-9][a-z0-9_-]{0,40}$/;
const URL_RX = /^https?:\/\/[^\s]+$/;
/** The budget scale: the hard limit in USD (0: no limit). The soft limit is 80% of it unless set under More. */
const LIMITS = [0, 5, 10, 25, 50, 100, 250, 500];

/** Whether a model server is on this machine or its network (free to call), not a paid cloud provider. */
export function isLocalModel(baseUrl: string | null | undefined): boolean {
  let host: string;
  try {
    host = new URL(baseUrl ?? "").hostname.replace(/^\[|\]$/g, "");
  } catch {
    return false;
  }
  return (
    !host.includes(".") || // a container next to Lens (ollama), localhost, ::1
    /^(127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|0\.0\.0\.0$)/.test(host) ||
    /\.(local|internal|lan|home\.arpa)$/.test(host)
  );
}

function errorText(e: unknown): string {
  return e instanceof ApiError ? e.message : "Something went wrong. Please try again.";
}

/**
 * First-run setup for a fresh install (the admin already exists). It asks only what has no answer yet and no default
 * (the server's `todo`): the model provider while there is none, then an overall AI budget for a paid (cloud) model (a
 * soft limit that warns, a hard limit that stops paid model calls; none unless chosen). With nothing left to ask (the
 * web installer set the model) it finishes on its own. The first namespace, storage, whether apps and AI assistants may
 * sign people in (OAuth, on unless turned off) and telemetry (off unless chosen) have defaults: their steps are a
 * click away, and in Settings. Each step can be skipped, and the whole wizard too. Fields .env sets are shown locked,
 * since the environment wins.
 */
export function SetupWizard() {
  const client = useApiClient();
  const router = useRouter();
  const queryClient = useQueryClient();
  const { me } = useArchive();
  const view = useQuery({
    queryKey: ["setup"],
    queryFn: () => data(Setup.getSetup({ client })),
  });
  const [step, setStep] = useState<StepId>("llm");
  const [done, setDone] = useState<Set<StepId>>(new Set(["admin"]));
  // A namespace seeded before the wizard (archive.yaml, LENS_NAMESPACE, an install script) counts as set up.
  const seeded = Boolean(view.data?.namespace.existing.length);
  useEffect(() => {
    if (seeded) setDone((d) => (d.has("namespace") ? d : new Set(d).add("namespace")));
  }, [seeded]);

  const finish = useMutation({
    mutationFn: (skipped: boolean) => data(Setup.finish({ client, body: { skipped } })),
    onSuccess: () => {
      queryClient.invalidateQueries();
      router.replace("/");
      router.refresh();
    },
  });

  // What to walk through, fixed when the wizard opens: the model provider (then the budget, if it's paid), or the
  // budget alone; nothing, and it finishes.
  const [flow, setFlow] = useState<StepId[] | null>(null);
  const [reached, setReached] = useState(0); // how far along the flow the person is
  useEffect(() => {
    if (!view.data || flow) return;
    const todo = view.data.todo ?? ["llm"];
    const f: StepId[] = todo.includes("llm") ? ["llm", "budget"] : [...todo];
    setFlow(f);
    if (f.length) setStep(f[0]);
    else finish.mutate(false);
  }, [view.data, flow, finish]);
  const ready = Boolean(flow?.length); // the steps show once it's known what to ask

  // Once a model answers, the assistant can do the rest by conversation (a setup chat makes the changes itself).
  const llmSet = Boolean(
    (view.data?.llm.values as { base_url?: string | null; model?: string | null } | undefined)?.model,
  );
  const withAssistant = useMutation({
    mutationFn: () => data(Setup.finish({ client, body: { skipped: false } })),
    onSuccess: () => {
      queryClient.invalidateQueries();
      router.replace("/chat?setup=1");
    },
  });

  // The budget step is for a paid (cloud) model; one on this machine or its network costs nothing, so it's skipped.
  const [modelUrl, setModelUrl] = useState<string | null>(null);
  const url = modelUrl ?? (view.data?.llm.values as { base_url?: string | null } | undefined)?.base_url ?? null;
  const flowFor = (u: string | null | undefined) =>
    (flow ?? []).filter((s) => s !== "budget" || (u && !isLocalModel(u)));
  const asked = flowFor(url);
  const steps = STEPS.filter((s) => s.id === "admin" || asked.includes(s.id));
  const more = STEPS.filter((s) => OPTIONAL.includes(s.id));

  // After a step of the flow, the next one; after one of the optional steps, back to where the person was. Past the
  // last, the wizard finishes.
  const next = (from: StepId, saved: boolean, baseUrl?: string) => {
    if (saved) setDone((d) => new Set(d).add(from));
    queryClient.invalidateQueries({ queryKey: ["setup"] });
    if (baseUrl !== undefined) setModelUrl(baseUrl);
    const shown = flowFor(baseUrl === undefined ? url : baseUrl);
    const i = shown.indexOf(from);
    const at = i >= 0 ? i + 1 : reached;
    if (i >= 0) setReached(at);
    if (at >= shown.length) finish.mutate(false);
    else setStep(shown[at]);
  };

  return (
    <div className="flex flex-col gap-5">
      <header className="flex items-center justify-between gap-3">
        <AuthBrand suffix="Setup" />
        <Button
          variant="ghost"
          size="sm"
          onClick={() => finish.mutate(true)}
          disabled={finish.isPending}
          title="Everything here stays changeable in Settings"
        >
          Skip setup
        </Button>
      </header>
      <div className="flex flex-col gap-1.5">
        <h1 className="text-[24px] font-bold leading-tight text-fg">Welcome to Lens</h1>
        <p className="text-[14px] leading-normal text-fg-secondary">
          Only what Lens can&apos;t choose for you is asked here; the rest has a default. Each step can be skipped and
          changed later in Settings; values set in the server&apos;s{" "}
          <code className="font-mono text-[12.5px] text-fg-strong">.env</code> are shown locked.
        </p>
      </div>

      {ready && (
        <>
          <ol className="grid grid-cols-2 gap-2 sm:grid-cols-3" aria-label="Setup steps">
            {steps.map((s, i) => {
              const on = s.id === step;
              const ok = done.has(s.id);
              return (
                <li key={s.id}>
                  <button
                    type="button"
                    disabled={s.id === "admin"}
                    onClick={() => setStep(s.id)}
                    aria-current={on ? "step" : undefined}
                    className={cn(
                      "flex w-full items-center gap-2 rounded-md border px-3 py-2 text-left text-[12.5px] font-semibold transition-colors",
                      on ? "border-blue bg-blue-surface text-fg" : "border-border bg-background text-fg-secondary",
                      s.id !== "admin" && !on && "hover:text-fg",
                    )}
                  >
                    <span
                      className={cn(
                        "flex size-5 shrink-0 items-center justify-center rounded-full text-[11px]",
                        ok ? "bg-green text-white" : on ? "bg-blue text-white" : "bg-surface-neutral text-fg-secondary",
                      )}
                      aria-hidden
                    >
                      {ok ? <Check className="size-3" /> : i + 1}
                    </span>
                    {s.label}
                  </button>
                </li>
              );
            })}
          </ol>
          <div className="-mt-2 flex flex-wrap items-center gap-1 text-[12.5px] text-fg-secondary">
            <span className="mr-1">With defaults, if you&apos;d like to choose:</span>
            {more.map((s) => (
              <Button
                key={s.id}
                variant="ghost"
                size="sm"
                onClick={() => setStep(s.id)}
                aria-current={s.id === step ? "step" : undefined}
                className={cn(s.id === step && "bg-blue-surface text-fg")}
              >
                {done.has(s.id) && <Check className="size-3 text-green" aria-hidden />}
                {s.label}
              </Button>
            ))}
          </div>
        </>
      )}

      {llmSet && step !== "llm" && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-blue-border bg-blue-surface px-4 py-3">
          <p className="text-[13.5px] text-fg">
            <span className="font-semibold">The model is set, so Lens is ready.</span> Keep the defaults for the rest,
            or talk it through with the assistant and drop files in as you go.
          </p>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => finish.mutate(false)}
              disabled={finish.isPending}
              title="Storage, apps and telemetry stay as they are; all changeable in Settings"
            >
              Finish with defaults
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={() => withAssistant.mutate()}
              disabled={withAssistant.isPending}
            >
              Finish with the assistant
            </Button>
          </div>
        </div>
      )}

      <section className="flex flex-col gap-4 rounded-xl border border-border bg-background p-6 sm:p-7">
        {view.error ? (
          <AuthAlert tone="error">{errorText(view.error)}</AuthAlert>
        ) : !ready || !view.data ? (
          <Skeleton className="h-40" />
        ) : step === "namespace" ? (
          <NamespaceStep view={view.data} onNext={(saved) => next("namespace", saved)} />
        ) : step === "llm" ? (
          <LlmStep view={view.data} onNext={(saved, baseUrl) => next("llm", saved, baseUrl)} />
        ) : step === "budget" ? (
          <BudgetStep view={view.data} onNext={(saved) => next("budget", saved)} />
        ) : step === "storage" ? (
          <StorageStep view={view.data} onNext={(saved) => next("storage", saved)} />
        ) : step === "apps" ? (
          <AppsStep view={view.data} onNext={(saved) => next("apps", saved)} />
        ) : (
          <TelemetryStep view={view.data} pending={finish.isPending} onNext={(saved) => next("telemetry", saved)} />
        )}
        {finish.error && <AuthAlert tone="error">{errorText(finish.error)}</AuthAlert>}
      </section>

      <p className="text-center text-[12.5px] text-fg-muted">
        Signed in as {me?.user.email ?? "the first admin"}
        {view.data?.admin.from_env ? " (created from LENS_ADMIN_EMAIL in .env)" : ""}.
      </p>
    </div>
  );
}

function StepHead({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <h2 className="text-[17px] font-bold text-fg">{title}</h2>
      <p className="text-[13.5px] leading-normal text-fg-secondary">{children}</p>
    </div>
  );
}

function Actions({
  onSkip,
  onSave,
  saveText = "Save and continue",
  pending,
  disabled,
}: {
  onSkip: () => void;
  onSave?: () => void;
  saveText?: string;
  pending?: boolean;
  disabled?: boolean;
}) {
  return (
    <div className="phone-actions flex flex-wrap items-center justify-end gap-2 pt-1 max-sm:-mx-6 max-sm:-mb-6 max-sm:rounded-b-xl max-sm:px-6 max-sm:pt-3">
      <Button variant="ghost" onClick={onSkip} disabled={pending}>
        Skip this step
      </Button>
      {onSave && (
        <Button variant="primary" onClick={onSave} disabled={pending || disabled}>
          {pending ? "Saving…" : saveText}
        </Button>
      )}
    </div>
  );
}

function LockedHint({ env }: { env: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      <Lock className="size-3" aria-hidden /> Set by <code className="font-mono">{env}</code> in .env
    </span>
  );
}

function NamespaceStep({ view, onNext }: { view: SetupView; onNext: (saved: boolean) => void }) {
  const client = useApiClient();
  const ns = view.namespace;
  const [name, setName] = useState("archive");
  const [graph, setGraph] = useState("shared");
  const save = useMutation({
    mutationFn: () => data(Setup.saveNamespace({ client, body: { name: name.trim(), graph: graph as "shared" } })),
    onSuccess: () => onNext(true),
  });
  const valid = NS.test(name.trim());

  if (ns.locked || ns.existing.length > 0)
    return (
      <>
        <StepHead title="Namespaces">
          Namespaces keep assets, people and members&apos; access apart.{" "}
          {ns.locked ? (
            <>
              This server&apos;s are set in archive.yaml or <code className="font-mono">LENS_NAMESPACE</code>, so there
              is nothing to choose here.
            </>
          ) : (
            "This server already has one, so you're set. Add more any time under Admin → Namespaces."
          )}
        </StepHead>
        <ul className="flex flex-wrap gap-2">
          {ns.existing.map((n) => (
            <li key={n} className="rounded-pill bg-surface-neutral px-3 py-1 font-mono text-[12.5px] text-fg-strong">
              {n}
            </li>
          ))}
        </ul>
        <div className="flex justify-end">
          <Button variant="primary" onClick={() => onNext(true)}>
            Continue
          </Button>
        </div>
      </>
    );

  return (
    <>
      <StepHead title="Create the first namespace">
        A namespace holds a set of recordings, documents and images, with its own people and access. You can add more
        later.
      </StepHead>
      <Field
        label="Name"
        hint="Lowercase letters, digits, - and _"
        error={name && !valid ? "Use lowercase letters, digits, - and _ (start with a letter or digit)" : undefined}
      >
        {({ id, describedBy, invalid }) => (
          <Input
            id={id}
            aria-describedby={describedBy}
            invalid={invalid}
            mono
            value={name}
            onChange={(e) => setName(e.target.value.toLowerCase())}
            autoComplete="off"
            spellCheck={false}
          />
        )}
      </Field>
      <Field
        label="Knowledge graph"
        hint="Shared joins people and places across namespaces; isolated keeps them apart."
      >
        {() => (
          <SegmentedChoice
            label="Knowledge graph"
            value={graph}
            onChange={setGraph}
            options={[
              { value: "shared", label: "Shared" },
              { value: "isolated", label: "Isolated" },
            ]}
            className="w-full max-w-[280px]"
          />
        )}
      </Field>
      {save.error && <AuthAlert tone="error">{errorText(save.error)}</AuthAlert>}
      <Actions
        onSkip={() => onNext(false)}
        onSave={() => save.mutate()}
        saveText="Create and continue"
        pending={save.isPending}
        disabled={!valid}
      />
    </>
  );
}

function LlmStep({ view, onNext }: { view: SetupView; onNext: (saved: boolean, baseUrl?: string) => void }) {
  const client = useApiClient();
  const v = view.llm.values as { base_url?: string | null; model?: string | null; api_key?: { set?: boolean } };
  const locked = new Set(view.llm.locked);
  const [baseUrl, setBaseUrl] = useState(v.base_url ?? "");
  const [model, setModel] = useState(v.model ?? "");
  const [apiKey, setApiKey] = useState("");
  const [tested, setTested] = useState<LlmTestResult | null>(null);
  useEffect(() => setTested(null), [baseUrl, model, apiKey]);

  const body = () => ({ base_url: baseUrl, model, api_key: apiKey || null });
  const save = useMutation({ mutationFn: () => data(Setup.saveLlm({ client, body: body() })) });
  const test = useMutation({
    mutationFn: async () => {
      await data(Setup.saveLlm({ client, body: body() }));
      return data(Admin.testLlm({ client }));
    },
    onSuccess: setTested,
  });
  const allLocked = ["base_url", "model", "api_key"].every((k) => locked.has(k));
  // Model servers already running nearby (Ollama, LM Studio, ...) fill the form in; they and cloud providers are a click.
  const canPick = !locked.has("base_url") && !locked.has("model");
  const { servers, cloud, looking } = useModelProviders(canPick);
  const filled = useRef(false);
  useEffect(() => {
    if (filled.current || !servers.length || v.base_url) return;
    filled.current = true;
    setBaseUrl(servers[0].base_url);
    setModel(servers[0].model);
  }, [servers, v.base_url]);
  const here = providerAt([...servers, ...cloud], baseUrl);
  const keyRef = useRef<HTMLInputElement>(null);
  const pick = (p: ModelProvider) => {
    setBaseUrl(p.base_url);
    setModel(p.model);
    // a cloud provider needs its key next: the field is ready for the paste
    if (p.key_url && !locked.has("api_key")) requestAnimationFrame(() => keyRef.current?.focus());
  };

  const field = (
    key: "base_url" | "model",
    label: string,
    value: string,
    set: (s: string) => void,
    hint: ReactNode,
    options?: string[],
  ) => (
    <Field label={label} hint={locked.has(key) ? <LockedHint env={LLM_ENV[key]} /> : hint}>
      {({ id, describedBy }) => (
        <>
          <Input
            id={id}
            aria-describedby={describedBy}
            mono
            value={value}
            onChange={(e) => set(e.target.value)}
            disabled={locked.has(key)}
            autoComplete="off"
            spellCheck={false}
            list={options?.length ? `${id}-options` : undefined}
          />
          {options?.length ? (
            <datalist id={`${id}-options`}>
              {options.map((o) => (
                <option key={o} value={o} />
              ))}
            </datalist>
          ) : null}
        </>
      )}
    </Field>
  );

  return (
    <>
      <StepHead title="Connect a model provider">
        Summaries, chat and descriptions use any OpenAI-compatible server: a cloud provider such as OpenAI or Anthropic
        (pick it and paste its key), or one you run yourself such as Ollama, llama.cpp, LM Studio or vLLM. Transcription
        runs on this server and needs none.
      </StepHead>
      {canPick && (
        <ModelProviderPicker servers={servers} cloud={cloud} looking={looking} baseUrl={baseUrl} onPick={pick} />
      )}
      {field("base_url", "Base URL", baseUrl, setBaseUrl, "For example http://localhost:11434/v1 (Ollama)")}
      {field(
        "model",
        "Model",
        model,
        setModel,
        here
          ? here.key_url
            ? `Suggested for ${here.name}; type another if you like`
            : `Pick one of the ${here.models.length} on ${here.name}, or type another`
          : "The model's name on that server, for example llama3.1",
        here?.models,
      )}
      <Field
        label="API key"
        optional={!locked.has("api_key")}
        hint={
          locked.has("api_key") ? (
            <LockedHint env={LLM_ENV.api_key} />
          ) : v.api_key?.set ? (
            "A key is saved; leave this empty to keep it"
          ) : here?.key_url ? (
            <>
              <KeyLink provider={here} />. Stored encrypted and never shown again.
            </>
          ) : (
            "Local servers usually need none. Stored encrypted and never shown again."
          )
        }
      >
        {({ id, describedBy }) => (
          <Input
            ref={keyRef}
            id={id}
            aria-describedby={describedBy}
            type="password"
            mono
            value={locked.has("api_key") ? "••••••••" : apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            disabled={locked.has("api_key")}
            autoComplete="off"
          />
        )}
      </Field>
      {tested &&
        (tested.ok ? (
          <AuthAlert tone="success">
            {tested.model} answered “{tested.reply}” in {tested.ms} ms.
          </AuthAlert>
        ) : (
          <AuthAlert tone="error">The model didn&apos;t answer: {tested.error}</AuthAlert>
        ))}
      {(save.error || test.error) && <AuthAlert tone="error">{errorText(save.error || test.error)}</AuthAlert>}
      <div className="phone-actions flex flex-wrap items-center justify-between gap-2 pt-1 max-sm:-mx-6 max-sm:-mb-6 max-sm:rounded-b-xl max-sm:px-6 max-sm:pt-3">
        <Button variant="secondary" onClick={() => test.mutate()} disabled={test.isPending || !baseUrl || !model}>
          {test.isPending ? "Testing…" : "Test connection"}
        </Button>
        <div className="flex flex-wrap gap-2">
          <Button variant="ghost" onClick={() => onNext(false)} disabled={save.isPending}>
            Skip this step
          </Button>
          <Button
            variant="primary"
            onClick={() =>
              allLocked ? onNext(true, baseUrl) : save.mutate(undefined, { onSuccess: () => onNext(true, baseUrl) })
            }
            disabled={save.isPending}
          >
            {save.isPending ? "Saving…" : allLocked ? "Continue" : "Save and continue"}
          </Button>
        </div>
      </div>
    </>
  );
}

function StorageStep({ view, onNext }: { view: SetupView; onNext: (saved: boolean) => void }) {
  const client = useApiClient();
  const s = view.storage;
  const [maxMb, setMaxMb] = useState(String(s.max_upload_mb));
  const [folder, setFolder] = useState("");
  const [namespace, setNamespace] = useState(view.namespace.existing[0] ?? "");
  // where Lens keeps its own files (notes' attachments): this machine, or a storage connection (docs/storage.md)
  const [where, setWhere] = useState<"local" | "connection">("local");
  const [conn, setConn] = useState("");
  const [bucket, setBucket] = useState("lens");
  const [crypt, setCrypt] = useState(false);
  const conns = useQuery({
    queryKey: ["sources"],
    queryFn: () => data(Sources.listSources({ client })),
    enabled: where === "connection",
  });
  const storage = (conns.data ?? []).filter((c) => !NOT_STORAGE.has(c.type));
  const files = fileStoreTry(where, conn, bucket, crypt);
  const [checked, setChecked] = useState<FileStoreTestResult | null>(null);
  const check = useMutation({
    mutationFn: () => data(Admin.testFileStore({ client, body: files })),
    onSuccess: setChecked,
  });
  useEffect(() => setChecked(null), [where, conn, bucket, crypt]);
  const save = useMutation({
    mutationFn: async () => {
      await data(
        Setup.saveStorage({
          client,
          body: {
            max_upload_mb: Number(maxMb),
            folder: folder.trim() || null,
            namespace: folder.trim() ? namespace : null,
          },
        }),
      );
      if (files.store === "connection") {
        const ok = checked?.ok ? checked : await data(Admin.testFileStore({ client, body: files }));
        if (!ok.ok) throw new Error(`That connection didn’t work: ${ok.error ?? "it couldn’t be written to"}`);
        await data(Admin.updateSettings({ client, path: { section: "files" }, body: files }));
      }
    },
    onSuccess: () => onNext(true),
  });
  const mb = Number(maxMb);
  const validMb = Number.isInteger(mb) && mb >= 1 && mb <= 1_000_000;

  return (
    <>
      <StepHead title="Storage">
        Where this server keeps its data and its own files, how large an upload may be, and optionally a folder on this
        machine to import from as files arrive.
      </StepHead>
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 rounded-md bg-surface px-4 py-3 text-[13px]">
        <dt className="font-semibold text-fg-strong">Data folder</dt>
        <dd className="break-all font-mono text-[12.5px] text-fg-secondary">{s.data_dir}</dd>
        <dt className="font-semibold text-fg-strong">Database</dt>
        <dd className="break-all font-mono text-[12.5px] text-fg-secondary">
          {s.embedded ? "embedded, in the data folder" : s.database}
        </dd>
      </dl>
      <p className="-mt-2 text-[12px] text-fg-muted">
        These are set where the server starts (<code className="font-mono">archive.yaml</code>,{" "}
        <code className="font-mono">SURREAL_URL</code> in .env), so the web app can&apos;t move them.
      </p>
      <Field label="Largest upload (MB)" error={validMb ? undefined : "A whole number from 1 to 1000000"}>
        {({ id, describedBy, invalid }) => (
          <Input
            id={id}
            aria-describedby={describedBy}
            invalid={invalid}
            inputMode="numeric"
            value={maxMb}
            onChange={(e) => setMaxMb(e.target.value.replace(/[^0-9]/g, ""))}
            className="max-w-[200px]"
          />
        )}
      </Field>
      {s.local_roots.length > 0 ? (
        <>
          <Field
            label="Watch a folder"
            optional
            hint={
              <>
                New files there are imported. It must be inside{" "}
                {s.local_roots.map((r, i) => (
                  <span key={r}>
                    {i > 0 && ", "}
                    <code className="font-mono">{r}</code>
                  </span>
                ))}
                .
              </>
            }
          >
            {({ id, describedBy }) => (
              <Input
                id={id}
                aria-describedby={describedBy}
                mono
                placeholder={s.local_roots[0]}
                value={folder}
                onChange={(e) => setFolder(e.target.value)}
                autoComplete="off"
                spellCheck={false}
              />
            )}
          </Field>
          {folder.trim() && view.namespace.existing.length > 1 && (
            <Field label="Into namespace">
              {() => (
                <SegmentedChoice
                  label="Into namespace"
                  value={namespace}
                  onChange={setNamespace}
                  options={view.namespace.existing.map((n) => ({ value: n, label: n }))}
                />
              )}
            </Field>
          )}
          {folder.trim() && view.namespace.existing.length === 0 && (
            <AuthAlert tone="gate">Create a namespace first (step 2) to watch a folder.</AuthAlert>
          )}
        </>
      ) : (
        <p className="text-[12.5px] text-fg-muted">
          To watch folders on this machine, list them under <code className="font-mono">sources.local_roots</code> in
          archive.yaml. Cloud storage (S3, Drive, Dropbox, SFTP…) can be added later under Sources.
        </p>
      )}
      <Field
        label="Keep Lens’s own files"
        hint="Notes’ attachments and other files Lens makes. Each is encrypted before it leaves this machine."
      >
        {() => (
          <SegmentedChoice
            label="Keep Lens’s own files"
            value={where}
            onChange={(v) => setWhere(v as "local" | "connection")}
            options={[
              { value: "local", label: "On this machine" },
              { value: "connection", label: "On a storage connection" },
            ]}
          />
        )}
      </Field>
      {where === "connection" && (
        <>
          <div className="flex flex-wrap items-end gap-2.5">
            {storage.length > 0 && (
              <Field label="Connection" className="min-w-[220px] flex-1">
                {({ id }) => (
                  <Select
                    id={id}
                    value={conn}
                    onChange={(e) => setConn(e.target.value)}
                    options={[
                      { value: "", label: "Choose a connection" },
                      ...storage.map((c) => ({ value: String(c.id), label: `${c.name} (${c.type})` })),
                    ]}
                  />
                )}
              </Field>
            )}
            <AddConnection
              label={storage.length ? "Add another" : "Add a connection"}
              onAdded={(id) => {
                void conns.refetch();
                setConn(String(id));
              }}
            />
          </div>
          <Field label="Folder" hint="For S3, the bucket and a path (my-bucket/lens); for a folder here, its full path">
            {({ id, describedBy }) => (
              <Input
                id={id}
                aria-describedby={describedBy}
                mono
                value={bucket}
                onChange={(e) => setBucket(e.target.value)}
                autoComplete="off"
                spellCheck={false}
              />
            )}
          </Field>
          <Switch
            checked={crypt}
            onCheckedChange={setCrypt}
            label="Also encrypt with rclone crypt, which hides file names and sizes there too"
          />
          <div className="flex flex-wrap items-center gap-2.5">
            <Button
              size="sm"
              onClick={() => check.mutate()}
              disabled={check.isPending || files.store !== "connection" || !files.connection}
            >
              {check.isPending ? "Checking…" : "Check it"}
            </Button>
            {checked && (
              <span className={cn("text-[12.5px]", checked.ok ? "text-green-dark" : "text-red-dark")}>
                {checked.ok ? `It works (${checked.seconds} s)` : checked.error}
              </span>
            )}
          </div>
        </>
      )}
      {save.error && <AuthAlert tone="error">{errorText(save.error)}</AuthAlert>}
      <Actions
        onSkip={() => onNext(false)}
        onSave={() => save.mutate()}
        pending={save.isPending}
        disabled={
          !validMb || (Boolean(folder.trim()) && !namespace) || (files.store === "connection" && !files.connection)
        }
      />
    </>
  );
}

/** Apps and AI assistants (MCP clients) signing people in with their Lens account: OAuth (docs/authentication.md). */
function money(n: number) {
  return `$${Number.isInteger(n) ? n : n.toFixed(2)}`;
}

function nearest(usd: number | null | undefined) {
  if (usd == null) return 0;
  let best = 1;
  LIMITS.forEach((l, i) => {
    if (i && Math.abs(l - usd) < Math.abs(LIMITS[best] - usd)) best = i;
  });
  return best;
}

function BudgetStep({ view, onNext }: { view: SetupView; onNext: (saved: boolean) => void }) {
  const client = useApiClient();
  const b = view.budget;
  const [level, setLevel] = useState(nearest(b.hard_usd));
  const [soft, setSoft] = useState(b.hard_usd != null && b.soft_usd != null ? String(b.soft_usd) : "");
  const [period, setPeriod] = useState<"day" | "week" | "month">(
    b.period === "day" || b.period === "week" ? b.period : "month",
  );
  const [onOver, setOnOver] = useState<"ask" | "skip">(b.on_over === "skip" ? "skip" : "ask");
  const [priceIn, setPriceIn] = useState(b.price_in != null ? String(b.price_in) : "");
  const [priceOut, setPriceOut] = useState(b.price_out != null ? String(b.price_out) : "");
  const hard = LIMITS[level];
  const num = (v: string) => (v.trim() === "" ? null : Number(v));
  const softN = num(soft);
  const badSoft = softN != null && (!(softN >= 0) || softN > hard);
  const pin = num(priceIn);
  const pout = num(priceOut);
  const badPrice = (pin == null) !== (pout == null) || (pin != null && !(pin >= 0)) || (pout != null && !(pout >= 0));
  const save = useMutation({
    mutationFn: () =>
      data(
        Setup.saveBudget({
          client,
          body: hard
            ? { hard_usd: hard, soft_usd: softN, period, on_over: onOver, price_in: pin, price_out: pout }
            : { hard_usd: null, price_in: pin, price_out: pout },
        }),
      ),
    onSuccess: () => onNext(true),
  });
  const per = period === "month" ? "a month" : period === "week" ? "a week" : "a day";

  return (
    <>
      <StepHead title="Budget">
        How much Lens may spend on AI models in all. Past the soft limit it warns you; at the hard limit, routines and
        workflows wait for you and paid model calls stop until you raise it. Models on your own machine cost nothing and
        keep working.
      </StepHead>
      <Field label="Hard limit" hint="Change it any time in Settings → Budgets.">
        {({ id, describedBy }) => (
          <div className="flex flex-col gap-2">
            <span className="text-[15px] font-semibold text-fg">{hard ? `${money(hard)} ${per}` : "No limit"}</span>
            <input
              id={id}
              aria-describedby={describedBy}
              type="range"
              min={0}
              max={LIMITS.length - 1}
              step={1}
              value={level}
              onChange={(e) => setLevel(Number(e.target.value))}
              className="w-full accent-(--aladdin-blue)"
            />
            <div className="flex justify-between text-[11.5px] text-fg-muted" aria-hidden>
              <span>None</span>
              <span>{money(LIMITS[LIMITS.length - 1])}</span>
            </div>
          </div>
        )}
      </Field>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Input price" hint={`USD per million tokens for ${b.model || "the chat model"}`}>
          {({ id, describedBy }) => (
            <Input
              id={id}
              aria-describedby={describedBy}
              inputMode="decimal"
              placeholder="3"
              value={priceIn}
              onChange={(e) => setPriceIn(e.target.value.replace(/[^0-9.]/g, ""))}
            />
          )}
        </Field>
        <Field
          label="Output price"
          hint="From your provider's price list"
          error={badPrice ? "Give both prices" : undefined}
        >
          {({ id, describedBy, invalid }) => (
            <Input
              id={id}
              aria-describedby={describedBy}
              invalid={invalid}
              inputMode="decimal"
              placeholder="15"
              value={priceOut}
              onChange={(e) => setPriceOut(e.target.value.replace(/[^0-9.]/g, ""))}
            />
          )}
        </Field>
      </div>
      {hard > 0 && (
        <details className="rounded-lg border border-border px-4 py-3 text-[13.5px]">
          <summary className="cursor-pointer font-semibold text-fg">More</summary>
          <div className="mt-3 grid gap-3 sm:grid-cols-3">
            <Field label="Soft limit (USD)" error={badSoft ? `From 0 to ${money(hard)}` : undefined}>
              {({ id, describedBy, invalid }) => (
                <Input
                  id={id}
                  aria-describedby={describedBy}
                  invalid={invalid}
                  inputMode="decimal"
                  placeholder={String(Math.round(hard * 80) / 100)}
                  value={soft}
                  onChange={(e) => setSoft(e.target.value.replace(/[^0-9.]/g, ""))}
                />
              )}
            </Field>
            <Field label="Per">
              {({ id, describedBy }) => (
                <Select
                  id={id}
                  aria-describedby={describedBy}
                  value={period}
                  onChange={(e) => setPeriod(e.target.value as "day" | "week" | "month")}
                  options={[
                    { value: "month", label: "Month" },
                    { value: "week", label: "Week" },
                    { value: "day", label: "Day" },
                  ]}
                />
              )}
            </Field>
            <Field label="At the limit, runs">
              {({ id, describedBy }) => (
                <Select
                  id={id}
                  aria-describedby={describedBy}
                  value={onOver}
                  onChange={(e) => setOnOver(e.target.value as "ask" | "skip")}
                  options={[
                    { value: "ask", label: "Wait for you" },
                    { value: "skip", label: "Are skipped" },
                  ]}
                />
              )}
            </Field>
          </div>
        </details>
      )}
      {hard > 0 && pin == null && (
        <p className="-mt-1 text-[12px] text-fg-muted">
          Without the model&apos;s price Lens can&apos;t count what it spends, so the limit only applies to models whose
          price is set in Settings → Telemetry.
        </p>
      )}
      {save.error && <AuthAlert tone="error">{errorText(save.error)}</AuthAlert>}
      <Actions
        onSkip={() => onNext(false)}
        onSave={() => save.mutate()}
        pending={save.isPending}
        disabled={badSoft || badPrice}
      />
    </>
  );
}

function AppsStep({ view, onNext }: { view: SetupView; onNext: (saved: boolean) => void }) {
  const client = useApiClient();
  const o = view.oauth;
  const [on, setOn] = useState(o.enabled ? "on" : "off");
  const [minutes, setMinutes] = useState(String(o.access_minutes));
  const [days, setDays] = useState(String(o.refresh_days));
  const [copied, setCopied] = useState(false);
  const mcp = mcpAddress();
  const mins = Number(minutes);
  const ds = Number(days);
  const validMinutes = Number.isInteger(mins) && mins >= 5 && mins <= 1440;
  const validDays = Number.isInteger(ds) && ds >= 1 && ds <= 3650;
  const save = useMutation({
    mutationFn: () =>
      data(
        Setup.saveOauth({
          client,
          body: on === "on" ? { enabled: true, access_minutes: mins, refresh_days: ds } : { enabled: false },
        }),
      ),
    onSuccess: () => onNext(true),
  });
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(mcp);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  return (
    <>
      <StepHead title="Apps and AI assistants">
        Apps and AI assistants such as Claude, ChatGPT or Cursor can sign in with someone&apos;s Lens account instead of
        asking them for a key. Each person approves an app once, it sees only what they can, and they can revoke it any
        time under API tokens.
      </StepHead>
      <Field
        label="Let apps sign in"
        hint="Off: apps need an API key. You can change this any time in Settings → API keys."
      >
        {() => (
          <SegmentedChoice
            label="Let apps sign in"
            value={on}
            onChange={setOn}
            options={[
              { value: "off", label: "Off" },
              { value: "on", label: "On" },
            ]}
            className="w-full max-w-[220px]"
          />
        )}
      </Field>
      {on === "on" && (
        <>
          <Field
            label="MCP server address"
            hint="Add this to an AI assistant as a connector; it asks people to sign in."
          >
            {({ id, describedBy }) => (
              <div className="flex gap-2">
                <Input id={id} aria-describedby={describedBy} mono readOnly value={mcp} />
                <Button variant="secondary" onClick={copy}>
                  {copied ? "Copied" : "Copy"}
                </Button>
              </div>
            )}
          </Field>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field
              label="Access token lasts (minutes)"
              hint="Apps renew it by themselves"
              error={validMinutes ? undefined : "A whole number from 5 to 1440"}
            >
              {({ id, describedBy, invalid }) => (
                <Input
                  id={id}
                  aria-describedby={describedBy}
                  invalid={invalid}
                  inputMode="numeric"
                  value={minutes}
                  onChange={(e) => setMinutes(e.target.value.replace(/[^0-9]/g, ""))}
                />
              )}
            </Field>
            <Field
              label="Apps stay signed in (days)"
              hint="Counted from when an app last renewed"
              error={validDays ? undefined : "A whole number from 1 to 3650"}
            >
              {({ id, describedBy, invalid }) => (
                <Input
                  id={id}
                  aria-describedby={describedBy}
                  invalid={invalid}
                  inputMode="numeric"
                  value={days}
                  onChange={(e) => setDays(e.target.value.replace(/[^0-9]/g, ""))}
                />
              )}
            </Field>
          </div>
        </>
      )}
      {save.error && <AuthAlert tone="error">{errorText(save.error)}</AuthAlert>}
      <Actions
        onSkip={() => onNext(false)}
        onSave={() => save.mutate()}
        pending={save.isPending}
        disabled={on === "on" && (!validMinutes || !validDays)}
      />
    </>
  );
}

function TelemetryStep({
  view,
  pending,
  onNext,
}: {
  view: SetupView;
  pending: boolean;
  onNext: (saved: boolean) => void;
}) {
  const client = useApiClient();
  const t = view.telemetry;
  const locked = new Set(t.locked);
  const [on, setOn] = useState(t.enabled ? "on" : "off");
  const [endpoint, setEndpoint] = useState(t.endpoint ?? "");
  const [tested, setTested] = useState<TelemetryTestResult | null>(null);
  useEffect(() => setTested(null), [endpoint]);
  const body = () => ({ enabled: on === "on", endpoint: endpoint.trim() || null });
  const save = useMutation({ mutationFn: () => data(Setup.saveTelemetry({ client, body: body() })) });
  const test = useMutation({
    mutationFn: async () => {
      // the test uses the saved address; saving it with telemetry off sends nothing else
      await data(Setup.saveTelemetry({ client, body: { enabled: t.enabled, endpoint: endpoint.trim() } }));
      return data(Admin.testTelemetry({ client }));
    },
    onSuccess: setTested,
  });
  const wantsEndpoint = on === "on" && !locked.has("endpoint");
  const badEndpoint = Boolean(endpoint.trim()) && !URL_RX.test(endpoint.trim());
  const missing = wantsEndpoint && !endpoint.trim();

  return (
    <>
      <StepHead title="Telemetry">
        Lens can send traces and metrics about its own work (requests, jobs, model calls with token counts and estimated
        cost) to an OpenTelemetry collector you run, to see how it performs. It is off unless you turn it on, and
        nothing is ever sent anywhere else.
      </StepHead>
      <Field
        label="Send telemetry"
        hint={
          locked.has("enabled") ? (
            <LockedHint env={TELEMETRY_ENV.enabled} />
          ) : (
            "You can change this any time in Settings → Telemetry."
          )
        }
      >
        {() => (
          <SegmentedChoice
            label="Send telemetry"
            value={on}
            onChange={locked.has("enabled") ? () => undefined : setOn}
            options={[
              { value: "off", label: "Off" },
              { value: "on", label: "On" },
            ]}
            className="w-full max-w-[220px]"
          />
        )}
      </Field>
      {on === "on" && (
        <>
          <Field
            label="OTLP endpoint"
            hint={
              locked.has("endpoint") ? (
                <LockedHint env={TELEMETRY_ENV.endpoint} />
              ) : (
                "Your collector's OTLP/HTTP address, for example http://localhost:4318"
              )
            }
            error={badEndpoint ? "Use an http(s) address" : undefined}
          >
            {({ id, describedBy, invalid }) => (
              <Input
                id={id}
                aria-describedby={describedBy}
                invalid={invalid}
                mono
                placeholder="http://localhost:4318"
                value={endpoint}
                onChange={(e) => setEndpoint(e.target.value)}
                disabled={locked.has("endpoint")}
                autoComplete="off"
                spellCheck={false}
              />
            )}
          </Field>
          <p className="-mt-2 text-[12px] text-fg-muted">
            Sent: route templates, job steps, record ids, model names, token counts, durations and costs. Never asset
            text, prompts, file names, titles or people.
          </p>
        </>
      )}
      {tested &&
        (tested.ok ? (
          <AuthAlert tone="success">The collector took a test span in {tested.ms} ms.</AuthAlert>
        ) : (
          <AuthAlert tone="error">The collector didn&apos;t take the test span: {tested.error}</AuthAlert>
        ))}
      {(save.error || test.error) && <AuthAlert tone="error">{errorText(save.error || test.error)}</AuthAlert>}
      <div className="phone-actions flex flex-wrap items-center justify-between gap-2 pt-1 max-sm:-mx-6 max-sm:-mb-6 max-sm:rounded-b-xl max-sm:px-6 max-sm:pt-3">
        {on === "on" ? (
          <Button
            variant="secondary"
            onClick={() => test.mutate()}
            disabled={test.isPending || badEndpoint || !endpoint.trim() || locked.has("endpoint")}
          >
            {test.isPending ? "Sending…" : "Send a test span"}
          </Button>
        ) : (
          <span />
        )}
        <div className="flex flex-wrap gap-2">
          <Button variant="ghost" onClick={() => onNext(false)} disabled={save.isPending || pending}>
            Skip this step
          </Button>
          <Button
            variant="primary"
            onClick={() =>
              locked.has("enabled") ? onNext(true) : save.mutate(undefined, { onSuccess: () => onNext(true) })
            }
            disabled={save.isPending || pending || badEndpoint || missing}
          >
            {save.isPending || pending ? "Saving…" : "Save and continue"}
          </Button>
        </div>
      </div>
    </>
  );
}
