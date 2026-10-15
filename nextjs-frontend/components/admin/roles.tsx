"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, Plus, ShieldCheck } from "lucide-react";
import { useState } from "react";

import { Users } from "@/app/openapi-client";
import { AdminFrame, useRoles } from "@/components/admin/admin-frame";
import { isUnreachable } from "@/components/errors/error-states";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Checkbox, Field, Input } from "@/components/ui/field";
import { EmptyState, SkeletonRows } from "@/components/ui/states";
import { Table, Td, Th, THead, Tr } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { Tooltip } from "@/components/ui/tooltip";
import { data, useApiClient } from "@/lib/api/browser";

type RoleRow = {
  name: string;
  label: string;
  description?: string;
  permissions: string[];
  builtin: boolean;
  rank: string;
  members?: number;
};
type Perm = { name: string; label: string; first: string };

/** "the-reviewers" from "The reviewers". */
const slug = (s: string) =>
  s
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^[^a-z]+|-+$/g, "")
    .slice(0, 40);

/** Roles (Admin): what each role may do in a namespace, and custom roles made from the permissions. */
export function RolesPage() {
  const roles = useRoles();
  const [dialog, setDialog] = useState<{ role?: RoleRow } | null>(null);
  const perms = (roles.data?.permissions ?? []) as Perm[];
  const list = (roles.data?.roles ?? []) as RoleRow[];
  return (
    <AdminFrame
      tab="roles"
      title="Roles"
      meta={roles.data ? `${list.length} roles · ${list.filter((r) => !r.builtin).length} custom` : undefined}
      actions={
        <Button variant="primary" size="sm" icon={<Plus />} onClick={() => setDialog({})}>
          New role
        </Button>
      }
    >
      {roles.isPending ? (
        <SkeletonRows rows={4} />
      ) : roles.isError ? (
        <EmptyState
          tone="error"
          icon={<ShieldCheck />}
          title={isUnreachable(roles.error) ? "Can’t reach the server" : "Couldn’t load the roles"}
          actions={<Button onClick={() => roles.refetch()}>Try again</Button>}
        >
          {roles.error.message}
        </EmptyState>
      ) : (
        <div className="overflow-x-auto rounded-md border border-border">
          <Table aria-label="Roles by permission">
            <THead>
              <Th>Role</Th>
              {perms.map((p) => (
                <Th key={p.name} className="text-center">
                  <Tooltip content={p.label}>
                    <span className="capitalize">{p.name}</span>
                  </Tooltip>
                </Th>
              ))}
              <Th className="text-right">People</Th>
            </THead>
            <tbody>
              {list.map((r) => (
                <Tr
                  key={r.name}
                  className={r.builtin ? undefined : "cursor-pointer"}
                  onClick={r.builtin ? undefined : () => setDialog({ role: r })}
                >
                  <Td>
                    <span className="flex flex-col gap-0.5">
                      <b className="text-[13.5px] font-semibold">{r.label}</b>
                      <span className="text-[12px] text-fg-muted">
                        {r.builtin ? "Built in" : `Custom · counts as ${r.rank} elsewhere`}
                        {r.description ? ` · ${r.description}` : ""}
                      </span>
                    </span>
                  </Td>
                  {perms.map((p) => (
                    <Td key={p.name} className="text-center">
                      {r.permissions.includes(p.name) ? (
                        <Check aria-label={`${r.label} can ${p.name}`} className="mx-auto size-4 text-fg-accent" />
                      ) : (
                        <span className="text-fg-muted" aria-label={`${r.label} can't ${p.name}`}>
                          —
                        </span>
                      )}
                    </Td>
                  ))}
                  <Td className="tabular text-right text-fg-secondary">{r.members ?? 0}</Td>
                </Tr>
              ))}
            </tbody>
          </Table>
        </div>
      )}
      <p className="text-[12.5px] leading-[1.45] text-fg-muted">
        Give a role to someone in a namespace from People or the namespace’s members. Built-in roles can’t change; a
        custom role always reads, and a change to it holds for everyone with it at once.
      </p>
      {dialog && <RoleDialog role={dialog.role} perms={perms} onClose={() => setDialog(null)} />}
    </AdminFrame>
  );
}

function RoleDialog({ role, perms, onClose }: { role?: RoleRow; perms: Perm[]; onClose: () => void }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const [label, setLabel] = useState(role?.label ?? "");
  const [description, setDescription] = useState(role?.description ?? "");
  const [chosen, setChosen] = useState<Set<string>>(new Set(role?.permissions ?? ["read"]));
  const name = role?.name ?? slug(label);
  const done = (title: string) => {
    void qc.invalidateQueries({ queryKey: ["roles"] });
    void qc.invalidateQueries({ queryKey: ["users"] });
    toast({ title, tone: "green" });
    onClose();
  };
  const save = useMutation({
    mutationFn: () => {
      const body = { label, description, permissions: [...chosen] };
      return role
        ? data(Users.updateRole({ client, path: { name: role.name }, body }))
        : data(Users.createRole({ client, body: { name, ...body } }));
    },
    onSuccess: () => done(role ? `Saved ${label}` : `Made the ${label} role`),
  });
  const remove = useMutation({
    mutationFn: () => data(Users.deleteRole({ client, path: { name: role!.name } })),
    onSuccess: () => done(`Deleted ${role!.label}`),
  });
  const ready = name.length >= 2 && label.trim().length > 0;
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={role ? `Edit ${role.label}` : "New role"}
      description={
        role ? `Everyone with this role (${role.members ?? 0}) gets the change at once.` : "Pick what it allows."
      }
      actions={
        <>
          {role && (
            <Button
              variant="danger-ghost"
              className="mr-auto"
              disabled={remove.isPending || Boolean(role.members)}
              disabledReason={role.members ? "Give its people another role first" : undefined}
              onClick={() => remove.mutate()}
            >
              Delete
            </Button>
          )}
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={!ready || save.isPending}
            disabledReason={!ready ? "Give it a name" : undefined}
            onClick={() => save.mutate()}
          >
            {save.isPending ? "Saving…" : role ? "Save" : "Make the role"}
          </Button>
        </>
      }
    >
      <form
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (ready) save.mutate();
        }}
      >
        <Field label="Name" hint={role ? undefined : name ? `Saved as ${name}` : undefined}>
          {(f) => <Input id={f.id} value={label} onChange={(e) => setLabel(e.target.value)} maxLength={60} autoFocus />}
        </Field>
        <Field label="Description" optional>
          {(f) => (
            <Input id={f.id} value={description} onChange={(e) => setDescription(e.target.value)} maxLength={300} />
          )}
        </Field>
        <fieldset className="flex flex-col gap-2">
          <legend className="mb-1 text-[13px] font-semibold">Permissions</legend>
          {perms.map((p) => (
            <Checkbox
              key={p.name}
              checked={p.name === "read" || chosen.has(p.name)}
              disabled={p.name === "read"}
              onCheckedChange={(v) =>
                setChosen((s) => {
                  const n = new Set(s);
                  if (v) n.add(p.name);
                  else n.delete(p.name);
                  return n;
                })
              }
              label={
                <>
                  <b className="font-semibold capitalize">{p.name}</b>{" "}
                  <span className="text-fg-secondary">· {p.label}</span>
                </>
              }
            />
          ))}
        </fieldset>
        <button type="submit" hidden />
      </form>
      {(save.isError || remove.isError) && <Banner tone="error">{(save.error ?? remove.error)?.message}</Banner>}
    </Dialog>
  );
}
