"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Undo2 } from "lucide-react";
import { useState, type FormEvent } from "react";

import { Mesh } from "@/app/openapi-client";
import type { MeshNode, MeshToken, MeshTokenCreated } from "@/app/openapi-client/types.gen";
import { ChoiceCards } from "@/components/settings/controls";
import { Badge } from "@/components/ui/badge";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Checkbox, Input } from "@/components/ui/field";
import { RenameTitle } from "@/components/ui/rename-title";
import { CodeBlock, Skeleton } from "@/components/ui/states";
import { data, useApiClient } from "@/lib/api/browser";
import { absolute, relative, shortDate } from "@/lib/format";
import { useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

/**
 * Settings › Nodes (docs/mesh.md): adding another machine with the line to run on it, every machine on the mesh with
 * how it's doing, renaming and removing one (with undo), and the join tokens not used yet.
 */

/** A node reports every minute, so one not heard from for three is offline. */
export const OFFLINE_AFTER_MS = 3 * 60_000;

const ROLES = [
  {
    value: "worker",
    label: "Worker",
    hint: "Transcribes and tells voices apart with its own models, against this install’s database, which it can read in full.",
  },
  {
    value: "compute",
    label: "Compute",
    hint: "Runs the assistant’s code in sandboxes on a gateway of its own (Linux 6.2 or newer, with Docker), and gets nothing of Lens’s: no database, no keys.",
  },
];
const ROLE_LABEL: Record<string, string> = { primary: "This machine", worker: "Worker", compute: "Compute" };

type Machine = { os?: string | null; arch?: string | null; cpus?: number | null; memory?: number | null };
type Report = {
  nebula?: string | boolean | null;
  version?: string | null;
  machine?: Machine | null;
  info?: Machine | null;
  error?: string | null;
  log?: string[] | null;
  sandbox?: { gateway?: string } | null;
};

const upper = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s);

/** "Online · seen 2 minutes ago", or offline once it hasn't reported for three minutes. */
export function seenText(lastSeen: string | null | undefined, now = Date.now()): { online: boolean; text: string } {
  const t = lastSeen ? Date.parse(lastSeen) : NaN;
  if (Number.isNaN(t)) return { online: false, text: "Not seen yet" };
  const online = now - t < OFFLINE_AFTER_MS;
  const ago = relative(lastSeen, now);
  return { online, text: online ? `Online · seen ${ago}` : `Offline · last seen ${ago}` };
}

/** "8 CPUs · 16 GB · Linux arm64" from what a node reports about its machine. */
export function machineText(m: Machine | null | undefined): string | null {
  if (!m) return null;
  const parts: string[] = [];
  if (m.cpus) parts.push(`${m.cpus} CPU${m.cpus === 1 ? "" : "s"}`);
  if (m.memory) {
    const gb = m.memory / 1024 ** 3;
    parts.push(`${gb < 10 ? Math.round(gb * 10) / 10 : Math.round(gb)} GB`);
  }
  const sys = [m.os, m.arch].filter(Boolean).join(" ");
  if (sys) parts.push(sys);
  return parts.length ? parts.join(" · ") : null;
}

function nebulaText(n: Report["nebula"]): string | null {
  if (n === true) return "Nebula running";
  if (n === false) return "Nebula not running";
  return n ? `Nebula ${n}` : null;
}

/** "works once, for 24 hours" from a token just made. */
export function tokenTerms(t: MeshToken, now = Date.now()): string {
  const uses = t.uses ?? 1;
  const times = uses === 1 ? "once" : `${uses} times`;
  if (!t.expires_at) return `works ${times}`;
  const from = t.created_at ? Date.parse(t.created_at) : now;
  const h = Math.max(1, Math.round((Date.parse(t.expires_at) - from) / 3.6e6));
  const span = h === 1 ? "an hour" : h <= 48 ? `${h} hours` : `${Math.round(h / 24)} days`;
  return `works ${times}, for ${span}`;
}

const stillGood = (n: MeshNode, now = Date.now()) => !n.not_after || Date.parse(n.not_after) > now;

export function NodesBody() {
  const client = useApiClient();
  const nodes = useQuery({
    queryKey: ["mesh-nodes"],
    queryFn: () => data(Mesh.meshNodes({ client })),
    refetchInterval: 15_000,
  });
  const tokens = useQuery({
    queryKey: ["mesh-tokens"],
    queryFn: () => data(Mesh.meshTokens({ client })),
    refetchInterval: 15_000,
  });
  // removed nodes stay listed, dimmed, while their certificate is still good (until then a removal can be undone)
  const shown = (nodes.data ?? []).filter((n) => !n.revoked_at || stillGood(n));
  return (
    <>
      <AddNode />
      <section className="flex flex-col gap-3" aria-label="Machines">
        <h3 className="border-t border-border pt-4 text-[15px] font-bold text-fg">Machines</h3>
        {nodes.isPending ? (
          <Skeleton className="h-16 w-full" />
        ) : nodes.isError ? (
          <Banner tone="error">{nodes.error.message}</Banner>
        ) : (
          <ul className="flex flex-col gap-2.5">
            {shown.map((n) => (
              <NodeRow key={n.id} node={n} />
            ))}
          </ul>
        )}
      </section>
      {(tokens.data ?? []).length > 0 && <Tokens list={tokens.data ?? []} />}
      <p className="text-[12.5px] leading-[1.45] text-fg-muted">
        Agents do the same over <code className="font-mono">/api/v1/mesh</code>, MCP and the assistant: nodes, tokens
        and the network; the assistant asks you before a machine joins or is taken off. A node joins, renews its
        certificate and reports here; a worker node takes this install’s jobs, and a compute node runs the assistant’s
        code.
      </p>
    </>
  );
}

function AddNode() {
  const client = useApiClient();
  const qc = useQueryClient();
  const { namespaces } = useArchive();
  const [role, setRole] = useState("worker");
  const [name, setName] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [made, setMade] = useState<MeshTokenCreated | null>(null);
  const add = useMutation({
    mutationFn: () =>
      data(
        Mesh.makeMeshToken({
          client,
          body: { role: role as "worker" | "compute", name: name.trim() || null, namespaces: picked },
        }),
      ),
    onSuccess: (r) => {
      setMade(r);
      setName("");
      setPicked([]);
      void qc.invalidateQueries({ queryKey: ["mesh-nodes"] });
      void qc.invalidateQueries({ queryKey: ["mesh-tokens"] });
      void qc.invalidateQueries({ queryKey: ["mesh"] });
      // adding a node turns the mesh on (mesh.enabled)
      void qc.invalidateQueries({ queryKey: ["settings"] });
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!add.isPending) add.mutate();
  };
  return (
    <section className="flex flex-col gap-3" aria-label="Add a node">
      <h3 className="text-[15px] font-bold text-fg">Add a node</h3>
      <form onSubmit={submit} className="flex flex-col gap-3">
        <ChoiceCards label="Role" options={ROLES} value={role} onChange={setRole} columns={2} size="sm" />
        <label className="flex flex-col gap-1.5">
          <span className="text-[13px] font-bold leading-tight text-fg-strong">
            Name <span className="ml-1 font-normal text-fg-muted">optional</span>
          </span>
          <Input
            autoFocus
            value={name}
            maxLength={63}
            placeholder="the machine’s own name"
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        {namespaces.length > 0 && (
          <div className="flex flex-col gap-1.5">
            <span className="text-[13px] font-bold leading-tight text-fg-strong">
              Namespaces it serves <span className="ml-1 font-normal text-fg-muted">none ticked: every one</span>
            </span>
            <div role="group" aria-label="Namespaces it serves" className="flex flex-wrap gap-x-3 gap-y-2">
              {namespaces.map((n) => (
                <Checkbox
                  key={n.name}
                  label={n.name}
                  checked={picked.includes(n.name)}
                  onCheckedChange={(on) => setPicked((p) => (on ? [...p, n.name] : p.filter((x) => x !== n.name)))}
                />
              ))}
            </div>
          </div>
        )}
        <div>
          <Button type="submit" variant="primary" size="sm" icon={<Plus />} disabled={add.isPending}>
            {add.isPending ? "Making the line…" : "Add node"}
          </Button>
        </div>
      </form>
      {add.isError && <Banner tone="error">{upper(add.error.message)}</Banner>}
      {made && (
        <div className="flex flex-col gap-2 rounded-md border border-green-border bg-green-surface p-3">
          <p className="text-[13.5px] leading-snug text-fg-strong">
            <b>Run it on the machine; it shows here when it joins.</b> The line {tokenTerms(made.token)} (until{" "}
            {absolute(made.token.expires_at)}) and isn’t shown again.
          </p>
          <CodeBlock text={made.install_line} label="the install line" />
          <div>
            <Button size="xs" variant="ghost" onClick={() => setMade(null)}>
              Done
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}

function NodeRow({ node: n }: { node: MeshNode }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const { namespaces } = useArchive();
  const put = (next: MeshNode) => {
    qc.setQueryData<MeshNode[]>(["mesh-nodes"], (list) => list?.map((x) => (x.id === next.id ? next : x)));
    void qc.invalidateQueries({ queryKey: ["mesh-nodes"] });
    void qc.invalidateQueries({ queryKey: ["mesh"] });
  };
  const remove = useMutation({
    mutationFn: () => data(Mesh.removeNode({ client, path: { node_id: n.id } })),
    onSuccess: put,
  });
  const restore = useMutation({
    mutationFn: () => data(Mesh.restoreNode({ client, path: { node_id: n.id } })),
    onSuccess: put,
  });
  const serve = useMutation({
    mutationFn: (list: string[]) =>
      data(Mesh.changeNode({ client, path: { node_id: n.id }, body: { namespaces: list } })),
    onSuccess: put,
  });
  const primary = n.role === "primary";
  const removed = Boolean(n.revoked_at);
  const onMesh = Boolean(n.ip); // before the first node there is no mesh, and this machine has no row to rename
  const r = (n.status ?? {}) as Report;
  const seen = seenText(n.last_seen);
  const facts = [
    n.ip,
    nebulaText(r.nebula),
    r.version && !primary ? `Lens ${r.version}` : null,
    machineText(r.machine ?? r.info),
    n.not_after ? `certificate until ${shortDate(n.not_after)}` : null,
    n.workers?.length ? `${n.workers.length} worker${n.workers.length === 1 ? "" : "s"}` : null,
    n.role === "compute" && !removed ? (r.sandbox?.gateway ? "sandbox gateway running" : "no sandbox gateway") : null,
  ].filter(Boolean);
  const served = n.namespaces ?? [];
  const error = remove.error ?? restore.error ?? serve.error;
  return (
    <li
      className={cn(
        "flex flex-col gap-2 rounded-md border border-border bg-background px-3.5 py-3",
        removed && "opacity-60",
      )}
      aria-label={n.name}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="min-w-0">
          <RenameTitle
            as="span"
            name={n.name}
            what="node"
            className="text-[14px] font-bold"
            inputClassName="h-8 w-[220px] text-[13.5px] font-semibold"
            onRename={
              onMesh && !removed
                ? async (name) => put(await data(Mesh.changeNode({ client, path: { node_id: n.id }, body: { name } })))
                : undefined
            }
          />
        </span>
        <Badge tone={primary ? "intent" : "neutral"}>{ROLE_LABEL[n.role] ?? n.role}</Badge>
        {removed ? (
          <Badge tone="red">Removed</Badge>
        ) : (
          !primary && (
            <span className="inline-flex items-center gap-1.5 text-[12.5px] text-fg-secondary">
              <span aria-hidden className={cn("size-2 rounded-full", seen.online ? "bg-green" : "bg-fg-muted")} />
              {seen.text}
            </span>
          )
        )}
        <span className="ml-auto">
          {removed ? (
            <Button size="xs" icon={<Undo2 />} onClick={() => restore.mutate()} disabled={restore.isPending}>
              Undo
            </Button>
          ) : (
            !primary && (
              <Button size="xs" variant="danger-ghost" onClick={() => remove.mutate()} disabled={remove.isPending}>
                Remove
              </Button>
            )
          )}
        </span>
      </div>
      {facts.length > 0 && (
        <p className="text-[12.5px] leading-[1.45] text-fg-secondary">
          {facts.map((f, i) => (
            <span key={i} className={cn(i === 0 && "font-mono")}>
              {i > 0 && " · "}
              {f}
            </span>
          ))}
        </p>
      )}
      {removed && (
        <p className="text-[12.5px] text-fg-muted">
          Removed {relative(n.revoked_at)}
          {n.revoked_by ? ` by ${n.revoked_by}` : ""}: its certificates are blocked on every machine. Undo while its
          certificate is still good{n.not_after ? `, until ${shortDate(n.not_after)}` : ""}.
        </p>
      )}
      {!primary && !removed && namespaces.length > 0 && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[12.5px]">
          <span className="font-semibold text-fg-secondary">
            {served.length ? "Serves" : "Serves every namespace, or only"}
          </span>
          {namespaces.map((ns) => (
            <Checkbox
              key={ns.name}
              label={ns.name}
              disabled={serve.isPending}
              checked={served.includes(ns.name)}
              onCheckedChange={(on) => serve.mutate(on ? [...served, ns.name] : served.filter((x) => x !== ns.name))}
            />
          ))}
        </div>
      )}
      {r.error && <Banner tone="error">{r.error}</Banner>}
      {error && <Banner tone="error">{upper(error.message)}</Banner>}
      {(r.log ?? []).length > 0 && (
        <details className="text-[12.5px]">
          <summary className="cursor-pointer font-semibold text-fg-secondary">
            {n.role === "worker" ? "What Nebula and its worker said" : "What Nebula said"}
          </summary>
          <pre className="mt-2 max-h-64 overflow-auto rounded-sm bg-surface-neutral p-2.5 font-mono text-[11.5px] leading-snug">
            {(r.log ?? []).join("\n")}
          </pre>
        </details>
      )}
    </li>
  );
}

function Tokens({ list }: { list: MeshToken[] }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const withdraw = useMutation({
    mutationFn: (id: number) => data(Mesh.withdrawMeshToken({ client, path: { token_id: id } })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["mesh-tokens"] });
      void qc.invalidateQueries({ queryKey: ["mesh"] });
    },
  });
  return (
    <section className="flex flex-col gap-2.5" aria-label="Join tokens not used yet">
      <h3 className="border-t border-border pt-4 text-[15px] font-bold text-fg">Join tokens not used yet</h3>
      <ul className="flex flex-col gap-1.5">
        {list.map((t) => (
          <li key={t.id} className="flex flex-wrap items-center gap-2 text-[13px]">
            <Badge>{ROLE_LABEL[t.role] ?? t.role}</Badge>
            <span className="font-semibold text-fg">{t.name || "named by the machine"}</span>
            <span className={cn("text-fg-secondary", t.expired && "text-red-dark")}>
              {t.expired
                ? "expired"
                : `${(t.uses ?? 1) - (t.used ?? 0)} use${(t.uses ?? 1) - (t.used ?? 0) === 1 ? "" : "s"} left, until ${absolute(t.expires_at)}`}
              {t.namespaces?.length ? ` · ${t.namespaces.join(", ")}` : ""}
            </span>
            <Button
              size="xs"
              variant="ghost"
              className="ml-auto"
              onClick={() => withdraw.mutate(t.id)}
              disabled={withdraw.isPending && withdraw.variables === t.id}
            >
              Withdraw
            </Button>
          </li>
        ))}
      </ul>
      {withdraw.isError && <Banner tone="error">{upper(withdraw.error.message)}</Banner>}
    </section>
  );
}
