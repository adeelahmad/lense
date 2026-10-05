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
import { AuthAlert, AuthBrand } from "@/components/auth/auth-card";
import { SegmentedChoice } from "@/components/settings/controls";
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
  { id: "storage", label: "Storage" },
  { id: "apps", label: "Apps and AI" },
  { id: "telemetry", label: "Telemetry" },
] as const;
type StepId = (typeof STEPS)[number]["id"];

/** The .env variable that sets each locked field (fastapi_backend/app/domain/settings.py, ENV_OVERRIDES). */
const LLM_ENV: Record<string, string> = {
  base_url: "LENS_LLM_BASE_URL",
  model: "LENS_LLM_MODEL",
  api_key: "LENS_LLM_API_KEY",
};
const TELEMETRY_ENV: Record<string, string> = { enabled: "LENS_TELEMETRY", endpoint: "LENS_TELEMETRY_ENDPOINT" };
const NS = /^[a-z0-9][a-z0-9_-]{0,40}$/;
const URL_RX = /^https?:\/\/[^\s]+$/;

function errorText(e: unknown): string {
  return e instanceof ApiError ? e.message : "Something went wrong. Please try again.";
}

/**
 * First-run setup for a fresh install (the admin already exists): the first namespace, the model provider, storage,
 * whether apps and AI assistants may sign people in (OAuth, on unless turned off) and whether to send telemetry (off
 * unless chosen), one step at a time. Each step can be skipped, and the whole wizard too; everything stays in Settings.
 * Fields .env sets are shown locked, since the environment wins.
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
  const [step, setStep] = useState<StepId>("namespace");
  const [done, setDone] = useState<Set<StepId>>(new Set(["admin"]));
  // A namespace seeded before the wizard (archive.yaml, LENS_NAMESPACE, an install script) counts as set up.
  const seeded = Boolean(view.data?.namespace.existing.length);
  useEffect(() => {
    if (seeded) setDone((d) => (d.has("namespace") ? d : new Set(d).add("namespace")));
  }, [seeded]);
  // With nothing to choose for the namespace (seeded, or set in archive.yaml), start at the model provider.
  const started = useRef(false);
  useEffect(() => {
    if (!view.data || started.current) return;
    started.current = true;
    if (seeded || view.data.namespace.locked) setStep((s) => (s === "namespace" ? "llm" : s));
  }, [view.data, seeded]);

  const finish = useMutation({
    mutationFn: (skipped: boolean) => data(Setup.finish({ client, body: { skipped } })),
    onSuccess: () => {
      queryClient.invalidateQueries();
      router.replace("/");
      router.refresh();
    },
  });

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

  const next = (from: StepId, saved: boolean) => {
    if (saved) setDone((d) => new Set(d).add(from));
    queryClient.invalidateQueries({ queryKey: ["setup"] });
    const i = STEPS.findIndex((s) => s.id === from);
    if (i === STEPS.length - 1) finish.mutate(false);
    else setStep(STEPS[i + 1].id);
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
        <h1 className="text-[24px] font-bold leading-[1.25] text-fg">Welcome to Lens</h1>
        <p className="text-[14px] leading-normal text-fg-secondary">
          A few choices get this server ready. Each step can be skipped and changed later in Settings; values set in the
          server&apos;s <code className="font-mono text-[12.5px] text-fg-strong">.env</code> are shown locked.
        </p>
      </div>

      <ol className="grid grid-cols-2 gap-2 sm:grid-cols-3" aria-label="Setup steps">
        {STEPS.map((s, i) => {
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
        {view.isPending ? (
          <Skeleton className="h-40" />
        ) : view.error ? (
          <AuthAlert tone="error">{errorText(view.error)}</AuthAlert>
        ) : step === "namespace" ? (
          <NamespaceStep view={view.data} onNext={(saved) => next("namespace", saved)} />
        ) : step === "llm" ? (
          <LlmStep view={view.data} onNext={(saved) => next("llm", saved)} />
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
    <div className="flex flex-wrap items-center justify-end gap-2 pt-1">
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
          Namespaces keep recordings, speakers and people&apos;s access apart.{" "}
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

function LlmStep({ view, onNext }: { view: SetupView; onNext: (saved: boolean) => void }) {
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
  // Model servers already running nearby (Ollama, LM Studio, ...): the first fills the form in, the rest are a click.
  const canPick = !locked.has("base_url") && !locked.has("model");
  const nearby = useQuery({
    queryKey: ["setup", "llm", "detect"],
    queryFn: () => data(Setup.detectLlm({ client })),
    enabled: canPick,
    staleTime: Infinity,
  });
  const servers = nearby.data ?? [];
  const filled = useRef(false);
  useEffect(() => {
    if (filled.current || !servers.length || v.base_url) return;
    filled.current = true;
    setBaseUrl(servers[0].base_url);
    setModel(servers[0].suggested);
  }, [servers, v.base_url]);
  const here = servers.find((s) => s.base_url === baseUrl);

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
        Summaries, chat and descriptions use any OpenAI-compatible server: OpenAI, or one you run yourself such as
        Ollama, llama.cpp, LM Studio or vLLM. Transcription runs on this server and needs none.
      </StepHead>
      {canPick && nearby.isPending && <p className="text-[12.5px] text-fg-muted">Looking for model servers nearby…</p>}
      {servers.length > 0 && (
        <div className="flex flex-col gap-1.5" role="group" aria-label="Model servers found">
          <p className="text-[12.5px] font-semibold text-fg">Found running nearby</p>
          <div className="flex flex-wrap gap-2">
            {servers.map((s) => (
              <button
                key={s.base_url}
                type="button"
                onClick={() => {
                  setBaseUrl(s.base_url);
                  setModel(s.suggested);
                }}
                aria-pressed={s.base_url === baseUrl}
                className={cn(
                  "flex flex-col rounded-md border px-3 py-1.5 text-left text-[12.5px]",
                  s.base_url === baseUrl ? "border-blue bg-blue-surface" : "border-border hover:border-blue-border",
                )}
              >
                <span className="font-semibold text-fg">{s.kind}</span>
                <span className="text-fg-muted">
                  {s.models.length} model{s.models.length === 1 ? "" : "s"} · {s.base_url}
                </span>
              </button>
            ))}
          </div>
        </div>
      )}
      {field("base_url", "Base URL", baseUrl, setBaseUrl, "For example http://localhost:11434/v1 (Ollama)")}
      {field(
        "model",
        "Model",
        model,
        setModel,
        here
          ? `Pick one of the ${here.models.length} on ${here.kind}, or type another`
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
          ) : (
            "Local servers usually need none. Stored encrypted and never shown again."
          )
        }
      >
        {({ id, describedBy }) => (
          <Input
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
      <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
        <Button variant="secondary" onClick={() => test.mutate()} disabled={test.isPending || !baseUrl || !model}>
          {test.isPending ? "Testing…" : "Test connection"}
        </Button>
        <div className="flex flex-wrap gap-2">
          <Button variant="ghost" onClick={() => onNext(false)} disabled={save.isPending}>
            Skip this step
          </Button>
          <Button
            variant="primary"
            onClick={() => (allLocked ? onNext(true) : save.mutate(undefined, { onSuccess: () => onNext(true) }))}
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
function AppsStep({ view, onNext }: { view: SetupView; onNext: (saved: boolean) => void }) {
  const client = useApiClient();
  const o = view.oauth;
  const [on, setOn] = useState(o.enabled ? "on" : "off");
  const [minutes, setMinutes] = useState(String(o.access_minutes));
  const [days, setDays] = useState(String(o.refresh_days));
  const [copied, setCopied] = useState(false);
  const mcp = typeof window === "undefined" ? "/mcp" : `${window.location.origin}/mcp`;
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
            Sent: route templates, job steps, record ids, model names, token counts, durations and costs. Never
            transcripts, prompts, file names, titles or people.
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
      <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
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
            {save.isPending || pending ? "Saving…" : "Save and finish"}
          </Button>
        </div>
      </div>
    </>
  );
}
