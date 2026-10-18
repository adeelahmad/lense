"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ClipboardPaste, Play, Plus, Server, Sparkles, X } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Extensions } from "@/app/openapi-client";
import type { McpConfig, McpPreset, McpServerFound } from "@/app/openapi-client/types.gen";
import { ServiceSwitch } from "@/components/admin/service-switch";
import { Badge } from "@/components/ui/badge";
import { Button, IconButton } from "@/components/ui/button";
import { Field, Input, Textarea } from "@/components/ui/field";
import { Panel } from "@/components/ui/panel";
import { EmptyState, PageHeader } from "@/components/ui/states";
import { Segmented } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { useArchive } from "@/lib/hooks/session";

/** Names whose values carry credentials: the server seals these when it saves them (actions_web.secret). */
export const SECRET_NAME = /auth|token|secret|key|password|cookie|signature|session/i;

type Pair = { key: string; value: string };
export type Draft = {
  name: string;
  description: string;
  how: "url" | "command";
  url: string;
  headers: Pair[];
  command: string;
  args: string;
  env: Pair[];
  /** Values written ${NAME}, to type in: put in their place when it's sent. */
  fill: string[];
  values: Record<string, string>;
  /** What the form doesn't show, kept as it came (a ready-made server's keep, off and ask). */
  extra: Record<string, unknown>;
};
type Tool = { name: string; description?: string; read_only?: boolean };

export const EMPTY: Draft = {
  name: "",
  description: "",
  how: "url",
  url: "",
  headers: [],
  command: "",
  args: "",
  env: [],
  fill: [],
  values: {},
  extra: {},
};

/** What the form shows of a server; the rest of its spec is kept as it came. */
const SHOWN = ["url", "headers", "command", "args", "env"];
const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
/** A value with the ${NAME}s typed in put in their place (as other apps write them: ${NAME}, ${input:NAME}). */
function filled(v: string, values: Record<string, string>) {
  return Object.entries(values).reduce(
    (out, [k, x]) =>
      x ? out.replace(new RegExp(`\\$\\{(?:input:|env:)?${escape(k)}(?::-[^}]*)?\\}`, "g"), () => x) : out,
    v,
  );
}

const pairs = (o: unknown): Pair[] =>
  Object.entries((o ?? {}) as Record<string, string>).map(([key, value]) => ({ key, value: String(value) }));
const object = (ps: Pair[], values: Record<string, string>) =>
  Object.fromEntries(ps.filter((p) => p.key.trim()).map((p) => [p.key.trim(), filled(p.value, values)]));

/** A server as the API takes it: {url, headers}, or {command, args, env}, with what the form doesn't show. */
export function specOf(d: Draft): Record<string, unknown> {
  if (d.how === "url") {
    const headers = object(d.headers, d.values);
    return { url: filled(d.url.trim(), d.values), ...(Object.keys(headers).length ? { headers } : {}), ...d.extra };
  }
  const args = d.args
    .split("\n")
    .map((a) => filled(a.trim(), d.values))
    .filter(Boolean);
  const env = object(d.env, d.values);
  return {
    command: d.command.trim(),
    ...(args.length ? { args } : {}),
    ...(Object.keys(env).length ? { env } : {}),
    ...d.extra,
  };
}

/** A server read from a pasted config, ready to change in the form. */
export function draftOf(f: McpServerFound): Draft {
  const s = f.spec as { url?: string; headers?: unknown; command?: string; args?: string[]; env?: unknown };
  const extra = Object.fromEntries(Object.entries(f.spec).filter(([k]) => !SHOWN.includes(k)));
  return {
    name: f.name,
    description: f.description ?? "",
    how: s.command ? "command" : "url",
    url: s.url ?? "",
    headers: pairs(s.headers),
    command: s.command ?? "",
    args: (s.args ?? []).join("\n"),
    env: pairs(s.env),
    fill: f.fill ?? [],
    values: {},
    extra,
  };
}

function manifestOf(d: Draft) {
  return {
    name: d.name.trim(),
    kind: "mcp",
    ...(d.description.trim() ? { description: d.description.trim() } : {}),
    ...specOf(d),
  };
}

/** Names and values (headers or env): a value whose name carries a credential is typed like a password. */
function Pairs({
  label,
  add,
  rows,
  onChange,
  fill,
}: {
  label: string;
  add: string;
  rows: Pair[];
  onChange: (rows: Pair[]) => void;
  fill: string[];
}) {
  const set = (i: number, p: Partial<Pair>) => onChange(rows.map((r, j) => (j === i ? { ...r, ...p } : r)));
  return (
    <fieldset className="flex flex-col gap-2">
      <legend className="mb-1.5 text-[13px] font-bold leading-tight text-fg-strong">
        {label}
        <span className="ml-1 font-normal text-fg-muted">optional</span>
      </legend>
      {rows.map((r, i) => {
        const placeholder = fill.find((f) => r.value.includes(`\${${f}`) || r.value.includes(`\${input:${f}`));
        return (
          <div key={i} className="flex flex-col gap-1">
            <div className="flex items-center gap-2">
              <Input
                aria-label={`${label}: name ${i + 1}`}
                mono
                className="max-w-[40%]"
                value={r.key}
                placeholder="Authorization"
                onChange={(ev) => set(i, { key: ev.target.value })}
              />
              <Input
                aria-label={`${label}: value of ${r.key || i + 1}`}
                mono
                type={SECRET_NAME.test(r.key) ? "password" : "text"}
                autoComplete="off"
                value={r.value}
                invalid={Boolean(placeholder)}
                onChange={(ev) => set(i, { value: ev.target.value })}
              />
              <IconButton
                label={`Remove ${r.key || "this one"}`}
                onClick={() => onChange(rows.filter((_, j) => j !== i))}
              >
                <X />
              </IconButton>
            </div>
            {placeholder && (
              <p className="text-[12.5px] text-red-dark">
                Fill in {placeholder}: type it in above, or here in its place.
              </p>
            )}
          </div>
        );
      })}
      <div>
        <Button size="sm" variant="ghost" icon={<Plus />} onClick={() => onChange([...rows, { key: "", value: "" }])}>
          {add}
        </Button>
      </div>
    </fieldset>
  );
}

/** Servers Lens knows, ready to add: one click (the browser, once its container runs), or a token to type in first
 * (GitHub), which `onPick` puts in the form. */
function ReadyMade({ onPick }: { onPick: (f: McpPreset) => void }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const router = useRouter();
  const q = useQuery({
    queryKey: ["mcp-presets"],
    queryFn: () => data(Extensions.mcpPresets({ client })),
    // while a container it needs starts
    refetchInterval: (q) =>
      q.state.data?.presets?.some((p) => p.service?.on && p.service.running === false) ? 3000 : false,
  });
  const add = useMutation({
    mutationFn: async (p: McpPreset) =>
      (await data(Extensions.createExtension({ client, body: { manifest: manifestOf(draftOf(p)) } }))).id,
    onSuccess: (id) => {
      void qc.invalidateQueries({ queryKey: ["extensions"] });
      toast({ tone: "green", title: "Added to the assistant" });
      router.push(`/extensions/${id}`);
    },
  });
  const presets = q.data?.presets ?? [];
  if (!presets.length) return null;
  return (
    <Panel title="Ready-made" subtitle="Servers Lens knows: one click, or a token to type in">
      <ul aria-label="Ready-made servers" className="flex flex-col divide-y divide-border">
        {presets.map((p) => {
          const stopped = Boolean(p.needs) && p.service?.running === false;
          return (
            <li key={p.name} className="flex flex-col gap-2 py-2.5 first:pt-0 last:pb-0">
              <div className="flex items-center gap-3">
                <Sparkles className="size-4 shrink-0 text-fg-muted" aria-hidden />
                <div className="min-w-0 flex-1">
                  <div className="text-[13.5px] font-semibold">{p.label}</div>
                  <div className="text-[12.5px] text-fg-muted">{p.description}</div>
                </div>
                {p.fill?.length ? (
                  <Button size="sm" onClick={() => onPick(p)}>
                    Set up {p.label}
                  </Button>
                ) : (
                  <Button size="sm" variant="primary" disabled={stopped || add.isPending} onClick={() => add.mutate(p)}>
                    {add.isPending && add.variables?.name === p.name ? "Adding…" : `Add ${p.label}`}
                  </Button>
                )}
              </div>
              {stopped && p.needs && (
                <ServiceSwitch profile={p.needs} label={`Run the ${p.label.toLowerCase()} on this machine`} />
              )}
              {add.isError && add.variables?.name === p.name && (
                <p role="alert" className="text-[13px] text-red-dark">
                  {add.error.message}
                </p>
              )}
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}

/** "Add MCP server": a short form (an address and its headers, or a command with its args and env), or a config
 * pasted from another app. Test asks the server for its tools; Enter saves. Tokens are sealed when saved. */
export function McpServerForm() {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const router = useRouter();
  const { admin } = useArchive();
  const [mode, setMode] = useState<"url" | "command" | "paste">("url");
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [pasted, setPasted] = useState("");
  const [found, setFound] = useState<McpConfig | null>(null);
  const [tools, setTools] = useState<Tool[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const edit = (d: Partial<Draft>) => {
    setDraft((old) => ({ ...old, ...d }));
    setTools(null);
    setError(null);
  };
  const pick = (f: McpServerFound) => {
    const d = draftOf(f);
    setDraft(d);
    setMode(d.how);
    setTools(null);
    setError(null);
  };

  const read = useMutation({
    mutationFn: () => data(Extensions.readMcpConfig({ client, body: { text: pasted } })),
    onSuccess: (r) => {
      setError(null);
      if (r.servers?.length === 1 && !r.skipped?.length) pick(r.servers[0]);
      else setFound(r);
    },
    onError: (e) => setError((e as Error).message),
  });
  const test = useMutation({
    mutationFn: () => data(Extensions.testMcpServer({ client, body: { spec: specOf(draft) } })),
    onSuccess: (r) => {
      setTools(r.tools as Tool[]);
      setError(null);
    },
    onError: (e) => setError((e as Error).message),
  });
  const save = useMutation({
    mutationFn: async (ds: Draft[]) => {
      const ids: number[] = [];
      for (const d of ds)
        ids.push((await data(Extensions.createExtension({ client, body: { manifest: manifestOf(d) } }))).id);
      return ids;
    },
    onSuccess: (ids) => {
      void qc.invalidateQueries({ queryKey: ["extensions"] });
      toast({ tone: "green", title: ids.length > 1 ? `Added ${ids.length} MCP servers` : "Added to the assistant" });
      router.push(ids.length > 1 ? "/extensions" : `/extensions/${ids[0]}`);
    },
    onError: (e) => setError((e as Error).message),
  });

  if (!admin)
    return (
      <EmptyState icon={<Server />} title="Only admins add MCP servers">
        An MCP server can reach other services and run programs, so an admin adds it. Ask one, or ask the assistant to
        list the servers already added.
      </EmptyState>
    );

  const ready = draft.name.trim() && (draft.how === "url" ? draft.url.trim() : draft.command.trim());
  return (
    <div className="flex flex-col gap-4 px-4 pb-10 pt-[18px] md:px-6">
      <div className="text-[13px]">
        <Link href="/extensions" className="text-fg-accent hover:underline">
          Extensions
        </Link>
      </div>
      <PageHeader
        title="Add MCP server"
        meta="Give the assistant another service's tools: a server on the network, or a program run in the MCP runner"
      />
      <ReadyMade onPick={pick} />
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
        <Panel
          title="Server"
          actions={
            <Segmented
              label="How it's reached"
              value={mode}
              onChange={(v: string) => {
                setMode(v as typeof mode);
                if (v !== "paste") edit({ how: v as Draft["how"] });
              }}
              items={[
                { value: "url", label: "Address" },
                { value: "command", label: "Command" },
                { value: "paste", label: "Paste a config", icon: <ClipboardPaste /> },
              ]}
            />
          }
        >
          {mode === "paste" ? (
            <form
              className="flex flex-col gap-3"
              onSubmit={(ev) => {
                ev.preventDefault();
                read.mutate();
              }}
            >
              <Field
                label="Config"
                hint="From Claude (.mcp.json, or mcpServers in its settings), VS Code (mcp.json) or a server's README: one server or many."
              >
                {({ id, describedBy }) => (
                  <Textarea
                    id={id}
                    aria-describedby={describedBy}
                    autoFocus
                    mono
                    rows={12}
                    spellCheck={false}
                    value={pasted}
                    placeholder='{"mcpServers": {"github": {"type": "http", "url": "https://api.githubcopilot.com/mcp/"}}}'
                    onChange={(ev) => setPasted(ev.target.value)}
                    onKeyDown={(ev) => {
                      if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) {
                        ev.preventDefault();
                        read.mutate();
                      }
                    }}
                  />
                )}
              </Field>
              {error && (
                <p role="alert" className="text-[13px] text-red-dark">
                  {error}
                </p>
              )}
              <div>
                <Button type="submit" variant="primary" disabled={!pasted.trim() || read.isPending}>
                  {read.isPending ? "Reading…" : "Read it"}
                </Button>
              </div>
              {found && (
                <div className="flex flex-col gap-2">
                  {(found.servers ?? []).length > 0 && (
                    <ul
                      aria-label="Servers in the config"
                      className="flex flex-col divide-y divide-border rounded-md border border-border"
                    >
                      {(found.servers ?? []).map((f) => (
                        <li key={f.name} className="flex items-center gap-3 px-3 py-2">
                          <div className="min-w-0 flex-1">
                            <div className="font-mono text-[13px] font-semibold">{f.name}</div>
                            <div className="truncate text-[12px] text-fg-muted">
                              {String(
                                (f.spec as { url?: string; command?: string }).url ??
                                  (f.spec as { command?: string }).command,
                              )}
                              {f.fill?.length ? `, fill in ${f.fill.join(", ")}` : ""}
                            </div>
                          </div>
                          <Button size="sm" onClick={() => pick(f)}>
                            Check and add
                          </Button>
                        </li>
                      ))}
                    </ul>
                  )}
                  {(found.skipped ?? []).map((s) => (
                    <p key={s.name} className="text-[13px] text-fg-secondary">
                      Left out <span className="font-mono">{s.name}</span>: {s.why}
                    </p>
                  ))}
                  {(found.servers ?? []).length > 1 && !(found.servers ?? []).some((f) => f.fill?.length) && (
                    <div>
                      <Button
                        variant="primary"
                        disabled={save.isPending}
                        onClick={() => save.mutate((found.servers ?? []).map(draftOf))}
                      >
                        {save.isPending ? "Adding…" : `Add all ${(found.servers ?? []).length}`}
                      </Button>
                    </div>
                  )}
                </div>
              )}
            </form>
          ) : (
            <form
              className="flex flex-col gap-3"
              onSubmit={(ev) => {
                ev.preventDefault();
                if (ready) save.mutate([draft]);
              }}
            >
              <Field label="Name" hint="What its tools are called by: name__tool. Lowercase letters, digits and _.">
                {({ id, describedBy }) => (
                  <Input
                    id={id}
                    aria-describedby={describedBy}
                    autoFocus
                    mono
                    value={draft.name}
                    placeholder="tracker"
                    onChange={(ev) => edit({ name: ev.target.value })}
                  />
                )}
              </Field>
              {draft.fill.length > 0 && (
                <fieldset className="flex flex-col gap-2">
                  <legend className="mb-1.5 text-[13px] font-bold leading-tight text-fg-strong">To type in</legend>
                  {draft.fill.map((f, i) => (
                    <Input
                      key={f}
                      aria-label={f}
                      mono
                      type="password"
                      autoComplete="off"
                      autoFocus={i === 0}
                      placeholder={f}
                      value={draft.values[f] ?? ""}
                      onChange={(ev) => edit({ values: { ...draft.values, [f]: ev.target.value } })}
                    />
                  ))}
                  <p className="text-[12.5px] text-fg-muted">
                    Its settings name these instead of giving them, as its app fills them in itself; what you type goes
                    in their place.
                  </p>
                </fieldset>
              )}
              {draft.how === "url" ? (
                <>
                  <Field label="Address" hint="Its Streamable HTTP address, usually ending in /mcp.">
                    {({ id, describedBy }) => (
                      <Input
                        id={id}
                        aria-describedby={describedBy}
                        mono
                        type="url"
                        value={draft.url}
                        placeholder="https://mcp.example.com/mcp"
                        onChange={(ev) => edit({ url: ev.target.value })}
                      />
                    )}
                  </Field>
                  <Pairs
                    label="Headers"
                    add="Add a header"
                    rows={draft.headers}
                    fill={draft.fill.filter((f) => !draft.values[f])}
                    onChange={(headers) => edit({ headers })}
                  />
                </>
              ) : (
                <>
                  <Field
                    label="Command"
                    hint="Run in the MCP runner, a sandbox with none of Lens's data or keys: usually npx or uvx."
                  >
                    {({ id, describedBy }) => (
                      <Input
                        id={id}
                        aria-describedby={describedBy}
                        mono
                        value={draft.command}
                        placeholder="npx"
                        onChange={(ev) => edit({ command: ev.target.value })}
                      />
                    )}
                  </Field>
                  <ServiceSwitch profile="mcp" label="Run the MCP runner on this machine" />
                  <Field label="Arguments" hint="One per line." optional>
                    {({ id, describedBy }) => (
                      <Textarea
                        id={id}
                        aria-describedby={describedBy}
                        mono
                        rows={3}
                        spellCheck={false}
                        value={draft.args}
                        placeholder={"-y\n@modelcontextprotocol/server-github"}
                        onChange={(ev) => edit({ args: ev.target.value })}
                      />
                    )}
                  </Field>
                  <Pairs
                    label="Environment"
                    add="Add a variable"
                    rows={draft.env}
                    fill={draft.fill.filter((f) => !draft.values[f])}
                    onChange={(env) => edit({ env })}
                  />
                </>
              )}
              <Field label="Description" optional>
                {({ id }) => (
                  <Input id={id} value={draft.description} onChange={(ev) => edit({ description: ev.target.value })} />
                )}
              </Field>
              <p className="text-[12.5px] text-fg-muted">
                Tokens, keys and passwords are sealed when it&apos;s saved and never shown again.
              </p>
              {error && (
                <p role="alert" className="text-[13px] text-red-dark">
                  {error}
                </p>
              )}
              <div className="flex flex-wrap gap-2">
                <Button
                  variant="secondary"
                  icon={<Play />}
                  disabled={!(draft.how === "url" ? draft.url.trim() : draft.command.trim()) || test.isPending}
                  onClick={() => test.mutate()}
                >
                  {test.isPending ? "Testing…" : "Test"}
                </Button>
                <Button type="submit" variant="primary" disabled={!ready || save.isPending}>
                  {save.isPending ? "Adding…" : "Add to the assistant"}
                </Button>
              </div>
            </form>
          )}
        </Panel>
        <Panel
          title="Its tools"
          subtitle={tools ? `${tools.length} listed by the server` : "Test it to see what it offers before adding it."}
        >
          {tools && tools.length > 0 && (
            <ul className="flex flex-col gap-2 text-[13px]">
              {tools.map((t) => (
                <li key={t.name} className="flex flex-col gap-0.5">
                  <span className="flex items-center gap-2">
                    <span className="font-mono font-semibold">{t.name}</span>
                    <Badge tone={t.read_only ? "green" : "gate"}>{t.read_only ? "reads" : "asks first"}</Badge>
                  </span>
                  {t.description && <span className="line-clamp-2 text-fg-muted">{t.description}</span>}
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </div>
  );
}
