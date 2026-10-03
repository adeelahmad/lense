"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, FolderPlus, Users as UsersIcon } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Namespaces, Pipelines, Users } from "@/app/openapi-client";
import { IpGroupsSection } from "@/components/access/ip-groups";
import { AdminFrame, usePeople } from "@/components/admin/admin-frame";
import { roleLabel, type Role } from "@/components/admin/people-model";
import { isUnreachable } from "@/components/errors/error-states";
import { NotificationsSection } from "@/components/notifications/notifications-section";
import { runtime } from "@/components/iiif/iiif-model";
import { ChoiceCards } from "@/components/settings/controls";
import { RoleChip } from "@/components/ui/badge";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select } from "@/components/ui/field";
import { Avatar, EmptyState, SkeletonRows } from "@/components/ui/states";
import { Table, Td, Th, THead, Tr } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { Tooltip } from "@/components/ui/tooltip";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { count, nsSlug } from "@/lib/format";
import { needRole, useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

const NS_RX = /^[a-z0-9][a-z0-9_-]{0,40}$/;
type Pipeline = { id: number; name: string; namespaces?: string[] };

/** Namespaces: admins see and create all; owners see the ones they own and manage their members. */
export function NamespacesPage() {
  const { namespaces, admin, roleIn, me } = useArchive();
  const people = usePeople();
  const [creating, setCreating] = useState(false);
  const shown = namespaces.filter((n) => admin || roleIn(n.name) === "owner");
  const members = (ns: string) => (people.data ?? []).filter((p) => !p.admin && p.roles?.[ns]).length;
  return (
    <AdminFrame
      tab="namespaces"
      title={admin ? "Namespaces" : "Your namespaces"}
      meta={`${count(shown.length)} namespace${shown.length === 1 ? "" : "s"}`}
      ownersToo
      actions={
        <Button
          variant="primary"
          size="sm"
          icon={<FolderPlus />}
          disabled={!admin}
          disabledReason="Admins create namespaces"
          onClick={() => setCreating(true)}
        >
          New namespace
        </Button>
      }
    >
      {!me ? (
        <div className="rounded-md border border-border">
          <SkeletonRows rows={3} />
        </div>
      ) : !shown.length ? (
        <EmptyState icon={<UsersIcon />} title="No namespaces yet">
          {admin ? "Create one, then import recordings into it." : "You don’t own a namespace."}
        </EmptyState>
      ) : (
        <div className="overflow-hidden rounded-md border border-border">
          <Table aria-label="Namespaces">
            <THead className="border-t-0">
              <tr>
                <Th>Namespace</Th>
                <Th>Recordings</Th>
                <Th>Graph</Th>
                {admin && <Th>Members</Th>}
                <Th>Your role</Th>
              </tr>
            </THead>
            <tbody>
              {shown.map((n) => (
                <Tr key={n.name} className="last:border-b-0">
                  <Td>
                    <Link href={`/admin/namespaces/${n.name}`} className="font-semibold text-fg hover:underline">
                      {n.name}
                    </Link>
                  </Td>
                  <Td className="tabular text-fg-secondary">
                    {count(n.recordings)} · {runtime(n.ms)}
                  </Td>
                  <Td className="text-fg-secondary">{n.graph === "isolated" ? "Isolated" : "Shared"}</Td>
                  {admin && <Td className="tabular text-fg-secondary">{people.data ? count(members(n.name)) : "…"}</Td>}
                  <Td>
                    <RoleChip role={roleIn(n.name) as Role} />
                  </Td>
                </Tr>
              ))}
            </tbody>
          </Table>
        </div>
      )}
      {creating && <CreateNamespace onClose={() => setCreating(false)} />}
    </AdminFrame>
  );
}

function CreateNamespace({ onClose }: { onClose: () => void }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const [name, setName] = useState("");
  const [graph, setGraph] = useState("shared");
  const create = useMutation({
    mutationFn: () =>
      data(
        Namespaces.createNamespace({
          client,
          body: { name: name.trim(), graph: graph as "shared" | "isolated" },
        }),
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["namespaces"] });
      void qc.invalidateQueries({ queryKey: ["me"] });
      toast({
        title: `Created ${name.trim()}`,
        body: "Add members, then import recordings into it.",
        tone: "green",
      });
      onClose();
    },
  });
  const err =
    name.trim() && !NS_RX.test(name.trim())
      ? "Lowercase letters, digits, - and _ (start with a letter or digit)"
      : null;
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="New namespace"
      description="A namespace holds recordings, speakers and a graph, with its own members."
      actions={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={!name.trim() || Boolean(err) || create.isPending}
            onClick={() => create.mutate()}
          >
            {create.isPending ? "Creating…" : "Create namespace"}
          </Button>
        </>
      }
    >
      <Field label="Name" error={err} hint="Shown in links, e.g. /iiif/collection/customer-calls">
        {(f) => (
          <Input
            id={f.id}
            aria-describedby={f.describedBy}
            invalid={f.invalid}
            mono
            value={name}
            onChange={(e) => setName(nsSlug(e.target.value))}
            placeholder="customer-calls"
            autoFocus
          />
        )}
      </Field>
      <div className="flex flex-col gap-2">
        <span className="text-[13px] font-bold text-fg-strong">Graph</span>
        <ChoiceCards
          label="Graph"
          value={graph}
          onChange={setGraph}
          options={[
            {
              value: "shared",
              label: "Shared",
              hint: "entities link with other shared namespaces",
            },
            {
              value: "isolated",
              label: "Isolated",
              hint: "its own graph only",
            },
          ]}
        />
      </div>
      {create.isError && <Banner tone="error">{create.error.message}</Banner>}
    </Dialog>
  );
}

/** One namespace: its members (Admin AD3, owners), its IP groups and its settings (graph, default pipeline). */
export function NamespaceDetail({ ns }: { ns: string }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const { namespaces, admin, can, me } = useArchive();
  const known = namespaces.find((n) => n.name === ns);
  const isOwner = can("owner", ns);
  const members = useQuery({
    queryKey: ["members", ns],
    queryFn: () => data(Users.listMembers({ client, path: { name: ns } })),
    enabled: Boolean(known) && isOwner,
  });
  const people = usePeople();
  const pipelines = useQuery({
    queryKey: ["pipelines"],
    queryFn: async () =>
      (await data(Pipelines.listPipelines({ client }))) as unknown as {
        pipelines: Pipeline[];
      },
    enabled: Boolean(known),
  });
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("editor");
  const [addError, setAddError] = useState<string | null>(null);

  const setMember = useMutation({
    mutationFn: (b: { email?: string; account?: number; role: Role | null }) =>
      data(Users.setMember({ client, path: { name: ns }, body: b })),
    onSuccess: (_r, b) => {
      void qc.invalidateQueries({ queryKey: ["members", ns] });
      void qc.invalidateQueries({ queryKey: ["users"] });
      if (b.email) {
        setEmail("");
        setAddError(null);
      }
      toast({
        title: b.role
          ? b.email
            ? `Added ${b.email} as ${b.role}`
            : `Role changed to ${b.role}`
          : "Removed from the namespace",
        tone: "green",
      });
    },
    onError: (e, b) => {
      if (b.email)
        setAddError(
          e instanceof ApiError && e.status === 404
            ? "No account with that email. Ask an admin to create it."
            : e.message,
        );
      else
        toast({
          title: "Couldn’t change the role",
          body: e.message,
          tone: "red",
        });
    },
  });
  const update = useMutation({
    mutationFn: (b: { graph?: "shared" | "isolated"; pipeline?: number | null }) =>
      data(Namespaces.updateNamespace({ client, path: { name: ns }, body: b })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["namespaces"] });
      void qc.invalidateQueries({ queryKey: ["pipelines"] });
      toast({ title: `Saved ${ns}`, tone: "green" });
    },
    onError: (e) => toast({ title: "Couldn’t save", body: e.message, tone: "red" }),
  });

  if (namespaces.length && !known)
    return (
      <AdminFrame tab="namespaces" title="Namespace" ownersToo>
        <EmptyState
          icon={<UsersIcon />}
          title="This namespace doesn’t exist"
          actions={
            <Button asChild>
              <Link href="/admin/namespaces">All namespaces</Link>
            </Button>
          }
        >
          Or you don’t have a role in it. Namespaces you don’t belong to are never shown here.
        </EmptyState>
      </AdminFrame>
    );

  const admins = admin ? (people.data ?? []).filter((p) => p.admin && !p.disabled) : [];
  const rows = (members.data ?? [])
    .slice()
    .sort((a, b) => (a.name || a.email || "").localeCompare(b.name || b.email || ""));
  const currentPipeline = (pipelines.data?.pipelines ?? []).find((p) => p.namespaces?.includes(ns));
  const validEmail = /^\S+@\S+\.\S+$/.test(email.trim());

  return (
    <AdminFrame
      tab="namespaces"
      title={ns}
      meta={
        known
          ? `${count(known.recordings)} recordings · ${known.graph === "isolated" ? "isolated" : "shared"} graph`
          : undefined
      }
      ownersToo
    >
      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,820px)_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-5">
          <section className="flex flex-col gap-3.5 rounded-md border border-border bg-background px-4 py-5 sm:px-6">
            <div className="flex flex-col gap-1">
              <h2 className="text-[20px] font-bold leading-tight text-fg">Members of {ns}</h2>
              <p className="text-[13px] leading-[1.4] text-fg-secondary">
                {admin ? "As a platform admin you own every namespace." : "You’re an owner here."} Only people who
                already have an account can be added; admins create accounts.
              </p>
            </div>
            {!isOwner ? (
              <Banner tone="info">{needRole("owner", ns)}.</Banner>
            ) : (
              <form
                className="grid items-start gap-2.5 sm:grid-cols-[minmax(0,1fr)_150px_auto]"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (validEmail) setMember.mutate({ email: email.trim(), role });
                }}
              >
                <Field label="Add by email" error={addError}>
                  {(f) => (
                    <Input
                      id={f.id}
                      aria-describedby={f.describedBy}
                      invalid={f.invalid}
                      type="email"
                      value={email}
                      onChange={(e) => (setEmail(e.target.value), setAddError(null))}
                    />
                  )}
                </Field>
                <Field label="Role">
                  {(f) => (
                    <Select
                      id={f.id}
                      value={role}
                      onChange={(e) => setRole(e.target.value as Role)}
                      options={["viewer", "editor", "owner"].map((r) => ({
                        value: r,
                        label: roleLabel(r as Role),
                      }))}
                    />
                  )}
                </Field>
                <Button type="submit" className="sm:mt-[22px]" disabled={!validEmail || setMember.isPending}>
                  Add
                </Button>
              </form>
            )}
            {members.isPending && isOwner ? (
              <SkeletonRows rows={3} />
            ) : members.isError ? (
              <EmptyState
                tone="error"
                icon={<UsersIcon />}
                title={isUnreachable(members.error) ? "Can’t reach the server" : "Couldn’t load the members"}
                actions={<Button onClick={() => members.refetch()}>Try again</Button>}
              >
                {members.error.message}
              </EmptyState>
            ) : (
              <ul className="flex flex-col">
                {admins.map((p) => (
                  <li
                    key={`a${p.id}`}
                    className="grid grid-cols-[32px_minmax(0,1fr)_150px_80px] items-center gap-3 border-t border-border py-2"
                  >
                    <Avatar name={p.name || p.email} size={32} className="border border-border" />
                    <span className="flex min-w-0 flex-col gap-[3px]">
                      <b className="truncate text-[13.5px] font-semibold leading-tight">
                        {p.name || p.email}
                        {p.id === me?.user.id ? " (you)" : ""}
                      </b>
                      <span className="truncate text-[12px] leading-none text-fg-muted">Platform admin</span>
                    </span>
                    <Tooltip content="Platform admins own every namespace">
                      <span className="flex h-8 items-center rounded-sm border border-border bg-surface px-2.5 text-[13px] font-medium text-fg-muted">
                        Owner
                      </span>
                    </Tooltip>
                    <span />
                  </li>
                ))}
                {rows.map((m) => {
                  const self = m.account === me?.user.id;
                  return (
                    <li
                      key={m.account}
                      className="grid grid-cols-[32px_minmax(0,1fr)_150px_80px] items-center gap-3 border-t border-border py-2"
                    >
                      <Avatar name={m.name || m.email} size={32} className="border border-border" />
                      <span className="flex min-w-0 flex-col gap-[3px]">
                        <b className="truncate text-[13.5px] font-semibold leading-tight">
                          {m.name || m.email}
                          {self ? " (you)" : ""}
                        </b>
                        <span className="truncate text-[12px] leading-none text-fg-muted">{m.email}</span>
                      </span>
                      <Tooltip content={self ? "You can’t change your own role here" : undefined}>
                        <span className="relative">
                          <select
                            aria-label={`${m.name || m.email}’s role`}
                            value={m.role}
                            disabled={self || !isOwner || setMember.isPending}
                            onChange={(e) =>
                              setMember.mutate({
                                account: m.account,
                                role: e.target.value as Role,
                              })
                            }
                            className={cn(
                              "h-8 w-full appearance-none rounded-sm border border-border pl-2.5 pr-7 text-[13px] font-medium outline-none focus:border-blue",
                              self ? "bg-surface text-fg-muted" : "bg-background text-fg",
                            )}
                          >
                            {["viewer", "editor", "owner"].map((r) => (
                              <option key={r} value={r}>
                                {roleLabel(r as Role)}
                              </option>
                            ))}
                          </select>
                          <ChevronDown
                            aria-hidden
                            className="pointer-events-none absolute right-2 top-1/2 size-[13px] -translate-y-1/2 text-fg-secondary"
                          />
                        </span>
                      </Tooltip>
                      <span className="text-right">
                        {!self && isOwner && (
                          <Button
                            variant="danger-ghost"
                            size="xs"
                            onClick={() => setMember.mutate({ account: m.account, role: null })}
                            aria-label={`Remove ${m.name || m.email}`}
                          >
                            Remove
                          </Button>
                        )}
                      </span>
                    </li>
                  );
                })}
                {!rows.length && !admins.length && (
                  <li className="border-t border-border py-3 text-[13px] text-fg-muted">
                    No members yet. Add people by their email.
                  </li>
                )}
              </ul>
            )}
          </section>
          <IpGroupsSection ns={ns} isOwner={isOwner} admin={admin} />
          <NotificationsSection ns={ns} isOwner={isOwner} admin={admin} />
        </div>

        <section className="flex flex-col gap-3.5 rounded-md border border-border bg-background px-4 py-5 sm:px-6">
          <h2 className="text-[17px] font-bold leading-tight text-fg">Settings</h2>
          <div className="flex flex-col gap-2">
            <span className="text-[13px] font-bold text-fg-strong">Graph</span>
            <ChoiceCards
              label="Graph"
              size="sm"
              value={known?.graph === "isolated" ? "isolated" : "shared"}
              onChange={(g) => update.mutate({ graph: g as "shared" | "isolated" })}
              disabled={!isOwner || update.isPending}
              disabledReason={!isOwner ? needRole("owner", ns) : undefined}
              options={[
                {
                  value: "shared",
                  label: "Shared",
                  hint: "links entities with other shared namespaces",
                },
                {
                  value: "isolated",
                  label: "Isolated",
                  hint: "its own graph only",
                },
              ]}
            />
          </div>
          <Field label="Default pipeline" hint="What runs on new recordings in this namespace">
            {(f) => (
              <Select
                id={f.id}
                aria-describedby={f.describedBy}
                value={currentPipeline ? String(currentPipeline.id) : ""}
                disabled={!isOwner || update.isPending || pipelines.isPending}
                onChange={(e) =>
                  update.mutate({
                    pipeline: e.target.value ? Number(e.target.value) : null,
                  })
                }
                options={[
                  { value: "", label: "Built-in standard pipeline" },
                  ...(pipelines.data?.pipelines ?? []).map((p) => ({
                    value: String(p.id),
                    label: p.name,
                  })),
                ]}
              />
            )}
          </Field>
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-[13px] font-semibold">
            <Link className="text-fg-accent hover:underline" href={`/iiif/collections/${ns}`}>
              IIIF Collection →
            </Link>
            <Link className="text-fg-accent hover:underline" href={`/iiif/collections/${ns}/profile`}>
              Metadata profile →
            </Link>
          </div>
        </section>
      </div>
    </AdminFrame>
  );
}
