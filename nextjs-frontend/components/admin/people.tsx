"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, Ellipsis, RefreshCw, UserPlus, Users as UsersIcon } from "lucide-react";
import { useRef, useState, type KeyboardEvent } from "react";

import { Users } from "@/app/openapi-client";
import { AdminFrame, usePeople } from "@/components/admin/admin-frame";
import {
  cellKey,
  describePending,
  roleLabel,
  tempPassword,
  withPending,
  type Pending,
  type Person,
  type Role,
} from "@/components/admin/people-model";
import { isUnreachable } from "@/components/errors/error-states";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select, Switch } from "@/components/ui/field";
import { Menu, MenuContent, MenuItem, MenuSeparator, MenuTrigger } from "@/components/ui/menu";
import { Avatar, CodeBlock, EmptyState, SkeletonRows } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { Tooltip } from "@/components/ui/tooltip";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { count, nameFromEmail, shortDate } from "@/lib/format";
import { useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

const ROLES: (Role | "")[] = ["", "viewer", "editor", "owner"];

type Dialogs =
  | { kind: "create" }
  | { kind: "reset"; person: Person }
  | { kind: "disable"; person: Person }
  | { kind: "rename"; person: Person };

/** People: accounts with their role in every namespace (Admin AD1); create, reset, disable (AD2). */
export function PeoplePage() {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const { me, namespaces } = useArchive();
  const people = usePeople();
  const [pending, setPending] = useState<Map<string, Pending>>(new Map());
  const [dialog, setDialog] = useState<Dialogs | null>(null);

  const list = (people.data ?? []) as Person[];
  const disabled = list.filter((p) => p.disabled).length;
  const nsNames = namespaces.map((n) => n.name);

  const save = useMutation({
    mutationFn: async () => {
      for (const p of pending.values()) {
        if ("admin" in p)
          await data(
            Users.updateUser({
              client,
              path: { uid: p.uid },
              body: { admin: p.admin },
            }),
          );
        else
          await data(
            Users.setMember({
              client,
              path: { name: p.ns },
              body: { account: p.uid, role: p.role },
            }),
          );
      }
    },
    onSuccess: () => {
      const n = pending.size;
      setPending(new Map());
      void qc.invalidateQueries({ queryKey: ["users"] });
      void qc.invalidateQueries({ queryKey: ["members"] });
      toast({
        title: `Saved ${n} change${n === 1 ? "" : "s"}`,
        body: "Each is recorded in the audit log.",
        tone: "green",
      });
    },
    onError: () => void qc.invalidateQueries({ queryKey: ["users"] }),
  });
  const enable = useMutation({
    mutationFn: (p: Person) =>
      data(
        Users.updateUser({
          client,
          path: { uid: p.id },
          body: { disabled: false },
        }),
      ),
    onSuccess: (_r, p) => {
      void qc.invalidateQueries({ queryKey: ["users"] });
      toast({
        title: `${p.name || p.email} can sign in again`,
        body: "Their roles and edits were kept.",
        tone: "green",
      });
    },
  });

  const changes = [...pending.values()];
  return (
    <AdminFrame
      tab="people"
      title="People"
      meta={
        people.data
          ? `${count(list.length)} account${list.length === 1 ? "" : "s"}${disabled ? ` · ${disabled} disabled` : ""}`
          : undefined
      }
      actions={
        <Button variant="primary" size="sm" icon={<UserPlus />} onClick={() => setDialog({ kind: "create" })}>
          Create account
        </Button>
      }
    >
      {people.isPending ? (
        <div className="rounded-md border border-border">
          <SkeletonRows rows={5} />
        </div>
      ) : people.isError ? (
        <EmptyState
          tone="error"
          icon={<UsersIcon />}
          title={isUnreachable(people.error) ? "Can’t reach the server" : "Couldn’t load the accounts"}
          actions={<Button onClick={() => people.refetch()}>Try again</Button>}
        >
          {people.error.message}
        </EmptyState>
      ) : (
        <RoleGrid
          people={list}
          namespaces={nsNames}
          pending={pending}
          myId={me?.user.id}
          onEdit={(p) => setPending((m) => withPending(m, list, p))}
          onAction={(kind, person) => (kind === "enable" ? enable.mutate(person) : setDialog({ kind, person }))}
        />
      )}
      {(changes.length > 0 || save.isError) && (
        <div
          className="flex flex-wrap items-center gap-2.5 rounded-md border border-blue-border bg-blue-surface px-3.5 py-2.5 text-[13px] font-medium leading-[1.3]"
          aria-live="polite"
        >
          <span aria-hidden className="size-2 rounded-full bg-blue" />
          <span className="min-w-0 flex-1">
            {changes.length} change{changes.length === 1 ? "" : "s"}:{" "}
            {changes
              .slice(0, 2)
              .map((c) => describePending(c, list))
              .join("; ")}
            {changes.length > 2 ? ` and ${changes.length - 2} more` : ""}
          </span>
          {save.isError && <span className="w-full text-red-dark sm:order-last">{save.error.message}</span>}
          <Button variant="ghost" size="sm" onClick={() => setPending(new Map())} disabled={save.isPending}>
            Discard
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={() => save.mutate()}
            disabled={save.isPending || !changes.length}
          >
            {save.isPending ? "Saving…" : "Save roles"}
          </Button>
        </div>
      )}
      <p className="text-[12.5px] leading-[1.45] text-fg-muted">
        Each cell is a role: — (none), Viewer, Editor or Owner. Arrow keys move between cells; Enter opens the role.
        Platform admins own every namespace.
      </p>
      {dialog?.kind === "create" && <CreateDialog onClose={() => setDialog(null)} />}
      {dialog?.kind === "reset" && <ResetDialog person={dialog.person} onClose={() => setDialog(null)} />}
      {dialog?.kind === "disable" && <DisableDialog person={dialog.person} onClose={() => setDialog(null)} />}
      {dialog?.kind === "rename" && <RenameDialog person={dialog.person} onClose={() => setDialog(null)} />}
    </AdminFrame>
  );
}

type Action = "reset" | "disable" | "enable" | "rename";

/** The role matrix, an ARIA grid: arrows move between cells, Enter opens the cell's control, Escape comes back. */
function RoleGrid({
  people,
  namespaces,
  pending,
  myId,
  onEdit,
  onAction,
}: {
  people: Person[];
  namespaces: string[];
  pending: Map<string, Pending>;
  myId?: number;
  onEdit: (p: Pending) => void;
  onAction: (kind: Action, p: Person) => void;
}) {
  // Interactive columns: 0 = platform admin, 1..n = namespaces, n+1 = the row's menu.
  const colCount = namespaces.length + 2;
  const [active, setActive] = useState<[number, number]>([0, 0]);
  const cells = useRef(new Map<string, HTMLDivElement>());
  const onlyAdmin = people.filter((p) => p.admin && !p.disabled).length <= 1;
  const cols = `minmax(200px,1.4fr) 104px ${namespaces.map(() => "minmax(116px,150px)").join(" ")} minmax(96px,130px) 32px`;

  const focusCell = (r: number, c: number) => {
    const rr = Math.max(0, Math.min(people.length - 1, r));
    const cc = Math.max(0, Math.min(colCount - 1, c));
    setActive([rr, cc]);
    cells.current.get(`${rr}:${cc}`)?.focus();
  };
  const onCellKey = (e: KeyboardEvent<HTMLDivElement>, r: number, c: number) => {
    if (e.target !== e.currentTarget) {
      if (e.key === "Escape") {
        e.preventDefault();
        e.currentTarget.focus();
      }
      return;
    }
    const moves: Record<string, [number, number]> = {
      ArrowUp: [-1, 0],
      ArrowDown: [1, 0],
      ArrowLeft: [0, -1],
      ArrowRight: [0, 1],
    };
    if (moves[e.key]) {
      e.preventDefault();
      focusCell(r + moves[e.key][0], c + moves[e.key][1]);
    } else if (e.key === "Home") {
      e.preventDefault();
      focusCell(e.ctrlKey ? 0 : r, 0);
    } else if (e.key === "End") {
      e.preventDefault();
      focusCell(e.ctrlKey ? people.length - 1 : r, colCount - 1);
    } else if (e.key === "Enter" || e.key === " " || e.key === "F2") {
      const control = e.currentTarget.querySelector<HTMLElement>("select,button,[role=switch]");
      if (!control || control.getAttribute("aria-disabled") === "true" || (control as HTMLButtonElement).disabled)
        return;
      e.preventDefault();
      if (control.tagName === "SELECT") {
        control.focus();
        try {
          (control as HTMLSelectElement).showPicker?.();
        } catch {
          /* picker needs a user gesture in some browsers; focus is enough */
        }
      } else control.click();
    }
  };
  const cellProps = (r: number, c: number) => ({
    role: "gridcell" as const,
    tabIndex: active[0] === r && active[1] === c ? 0 : -1,
    ref: (el: HTMLDivElement | null) => {
      if (el) cells.current.set(`${r}:${c}`, el);
      else cells.current.delete(`${r}:${c}`);
    },
    onFocus: (e: React.FocusEvent<HTMLDivElement>) => {
      if (e.target === e.currentTarget) setActive([r, c]);
    },
    onKeyDown: (e: KeyboardEvent<HTMLDivElement>) => onCellKey(e, r, c),
    className: "flex min-w-0 items-center rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-blue",
  });

  if (!people.length)
    return (
      <EmptyState icon={<UsersIcon />} title="No accounts yet">
        Create one to get started.
      </EmptyState>
    );

  return (
    <div className="relative overflow-x-auto rounded-md border border-border">
      <div
        role="grid"
        aria-label="Roles: people by namespaces"
        aria-rowcount={people.length + 1}
        aria-colcount={colCount + 2}
        className="min-w-fit text-[13px] leading-[1.3]"
      >
        <div
          role="row"
          className="grid items-center gap-3 bg-surface px-4 py-2.5 text-[12px] font-semibold leading-[1.2] text-fg-secondary"
          style={{ gridTemplateColumns: cols }}
        >
          <span role="columnheader">Person</span>
          <span role="columnheader">Platform admin</span>
          {namespaces.map((n) => (
            <span role="columnheader" key={n} className="truncate">
              {n}
            </span>
          ))}
          <span role="columnheader">Last sign-in</span>
          <span role="columnheader">
            <span className="sr-only">Actions</span>
          </span>
        </div>
        {people.map((p, r) => {
          const me = p.id === myId;
          const adminPending = pending.get(cellKey(p.id, "admin"));
          const isAdmin = adminPending && "admin" in adminPending ? adminPending.admin : Boolean(p.admin);
          return (
            <div
              role="row"
              key={p.id}
              className={cn("grid h-[60px] items-center gap-3 border-t border-border px-4", p.disabled && "opacity-55")}
              style={{ gridTemplateColumns: cols }}
            >
              <div role="rowheader" className="flex min-w-0 items-center gap-2.5">
                <Avatar
                  name={p.name || p.email}
                  size={32}
                  className="border border-border"
                  disabled={Boolean(p.disabled)}
                />
                <span className="flex min-w-0 flex-col gap-[3px]">
                  <b className="truncate text-[13.5px] font-semibold leading-[1.2]">
                    {p.name || p.email}
                    {me ? " (you)" : ""}
                    {p.disabled ? " · disabled" : ""}
                  </b>
                  <span className="truncate text-[12px] leading-none text-fg-muted">{p.email}</span>
                </span>
              </div>
              <div {...cellProps(r, 0)}>
                <Tooltip
                  content={
                    me
                      ? "You can’t remove your own admin rights"
                      : isAdmin && onlyAdmin && p.admin
                        ? "The only admin can’t lose admin rights"
                        : undefined
                  }
                >
                  <span>
                    <GridSwitch
                      label={`${p.name || p.email} is a platform admin`}
                      checked={isAdmin}
                      changed={Boolean(adminPending)}
                      disabled={me || (onlyAdmin && Boolean(p.admin))}
                      onChange={(v) => onEdit({ uid: p.id, admin: v })}
                    />
                  </span>
                </Tooltip>
              </div>
              {namespaces.map((ns, i) => {
                const pend = pending.get(cellKey(p.id, ns));
                const role = pend && "role" in pend ? pend.role : (p.roles?.[ns] ?? null);
                const changed = Boolean(pend);
                return (
                  <div key={ns} {...cellProps(r, i + 1)}>
                    {isAdmin ? (
                      <Tooltip content="Platform admins own every namespace">
                        <span className="flex h-8 w-full items-center rounded-sm border border-border bg-surface px-2.5 text-[13px] font-medium text-fg-muted">
                          Owner
                        </span>
                      </Tooltip>
                    ) : (
                      <span className="relative w-full">
                        <select
                          tabIndex={-1}
                          aria-label={`${p.name || p.email} in ${ns}`}
                          value={role ?? ""}
                          onChange={(e) =>
                            onEdit({
                              uid: p.id,
                              ns,
                              role: (e.target.value || null) as Role | null,
                            })
                          }
                          className={cn(
                            "h-8 w-full appearance-none rounded-sm border pl-2.5 pr-7 text-[13px] outline-none focus:border-blue",
                            role ? "font-medium text-fg" : "font-normal text-fg-muted",
                            changed ? "border-blue bg-blue-surface" : "border-border bg-background",
                          )}
                        >
                          {ROLES.map((x) => (
                            <option key={x} value={x}>
                              {roleLabel((x || null) as Role | null)}
                            </option>
                          ))}
                        </select>
                        <ChevronDown
                          aria-hidden
                          className="pointer-events-none absolute right-2 top-1/2 size-[13px] -translate-y-1/2 text-fg-secondary"
                        />
                      </span>
                    )}
                  </div>
                );
              })}
              <span className="tabular text-[12.5px] leading-[1.3] text-fg-secondary">
                {p.last_login_at ? shortDate(p.last_login_at, true) : "never"}
              </span>
              <div
                {...cellProps(r, colCount - 1)}
                className="flex justify-end rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-blue"
              >
                <Menu>
                  <MenuTrigger asChild>
                    <button
                      type="button"
                      tabIndex={-1}
                      aria-label={`Actions for ${p.name || p.email}`}
                      className="grid size-8 place-items-center rounded-full text-fg-muted hover:bg-surface-neutral"
                    >
                      <Ellipsis className="size-[18px]" />
                    </button>
                  </MenuTrigger>
                  <MenuContent className="w-[220px]">
                    <MenuItem onSelect={() => onAction("rename", p)}>Rename…</MenuItem>
                    <MenuItem onSelect={() => onAction("reset", p)}>Reset password…</MenuItem>
                    <MenuSeparator />
                    {p.disabled ? (
                      <MenuItem onSelect={() => onAction("enable", p)}>Enable account</MenuItem>
                    ) : (
                      <MenuItem danger disabled={me} onSelect={() => onAction("disable", p)}>
                        {me ? "You can’t disable yourself" : "Disable account…"}
                      </MenuItem>
                    )}
                  </MenuContent>
                </Menu>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** A switch that stays out of the tab order, so the grid keeps a single tab stop (arrows move between cells). */
function GridSwitch({
  label,
  checked,
  changed,
  disabled,
  onChange,
}: {
  label: string;
  checked: boolean;
  changed?: boolean;
  disabled?: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      aria-disabled={disabled || undefined}
      tabIndex={-1}
      onClick={() => !disabled && onChange(!checked)}
      className={cn(
        "relative h-5 w-9 shrink-0 rounded-[10px] transition-colors duration-base ease-standard",
        checked ? "bg-blue" : "bg-border",
        changed && "ring-2 ring-blue-border ring-offset-1 ring-offset-background",
        disabled && "cursor-not-allowed opacity-50",
      )}
    >
      <span
        className={cn(
          "absolute top-0.5 block size-4 rounded-full bg-white shadow-1 transition-transform duration-base ease-standard",
          checked ? "translate-x-[18px]" : "translate-x-0.5",
        )}
      />
    </button>
  );
}

function PasswordField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <Field label="Temporary password" hint="At least 10 characters. Share it privately.">
      {(f) => (
        <div className="flex gap-2">
          <Input
            id={f.id}
            aria-describedby={f.describedBy}
            mono
            value={value}
            onChange={(e) => onChange(e.target.value)}
            autoComplete="off"
            spellCheck={false}
          />
          <Button
            size="md"
            variant="ghost"
            icon={<RefreshCw />}
            onClick={() => onChange(tempPassword())}
            aria-label="Suggest another password"
          />
        </div>
      )}
    </Field>
  );
}

function Done({ title, password }: { title: string; password: string }) {
  return (
    <div className="flex flex-col gap-2">
      <Banner tone="success">{title}</Banner>
      <span className="text-[13px] text-fg-secondary">
        Share the temporary password privately. It isn’t shown again.
      </span>
      <CodeBlock text={password} label="password" />
    </div>
  );
}

function CreateDialog({ onClose }: { onClose: () => void }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const { namespaces, namespace: topNs } = useArchive();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState(() => tempPassword());
  const [admin, setAdmin] = useState(false);
  const [ns, setNs] = useState(topNs ?? namespaces[0]?.name ?? "");
  const [role, setRole] = useState<"none" | "viewer" | "editor" | "owner">("viewer");
  const shownName = name.trim() || nameFromEmail(email);
  const create = useMutation({
    mutationFn: async () => {
      const made = await data(
        Users.createUser({
          client,
          body: {
            name: shownName || null,
            email: email.trim(),
            password,
            admin,
          },
        }),
      );
      // a role in a namespace in the same step, so nobody is sent to the role matrix afterwards
      if (!admin && ns && role !== "none")
        await data(Users.setMember({ client, path: { name: ns }, body: { account: made.id, role } }));
      return made;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["users"] });
      void qc.invalidateQueries({ queryKey: ["members"] });
    },
  });
  const err = create.error instanceof ApiError ? create.error.message : create.error?.message;
  const emailErr = err && /email/i.test(err) ? err[0].toUpperCase() + err.slice(1) : null;
  const ready = /^\S+@\S+\.\S+$/.test(email.trim()) && password.length >= 10;
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Create account"
      actions={
        create.isSuccess ? (
          <Button variant="primary" onClick={onClose}>
            Done
          </Button>
        ) : (
          <>
            <Button variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button
              variant="primary"
              disabled={!ready || create.isPending}
              disabledReason={!ready ? "Enter an email and a password of at least 10 characters" : undefined}
              onClick={() => create.mutate()}
            >
              {create.isPending ? "Creating…" : "Create account"}
            </Button>
          </>
        )
      }
    >
      {create.isSuccess ? (
        <Done title={`Created ${shownName || email.trim()}.`} password={password} />
      ) : (
        <>
          <Field label="Email" error={emailErr}>
            {(f) => (
              <Input
                id={f.id}
                aria-describedby={f.describedBy}
                invalid={f.invalid}
                type="email"
                value={email}
                onChange={(e) => (setEmail(e.target.value), create.reset())}
                autoFocus
              />
            )}
          </Field>
          <Field label="Name" optional>
            {(f) => (
              <Input
                id={f.id}
                value={name}
                placeholder={nameFromEmail(email) || undefined}
                onChange={(e) => setName(e.target.value)}
              />
            )}
          </Field>
          <PasswordField value={password} onChange={setPassword} />
          <Switch checked={admin} onCheckedChange={setAdmin} label="Platform admin" />
          {!admin && namespaces.length > 0 && (
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Namespace">
                {(f) => (
                  <Select
                    id={f.id}
                    value={ns}
                    onChange={(e) => setNs(e.target.value)}
                    options={namespaces.map((n) => n.name)}
                  />
                )}
              </Field>
              <Field label="Role there">
                {(f) => (
                  <Select
                    id={f.id}
                    value={role}
                    onChange={(e) => setRole(e.target.value as typeof role)}
                    options={[
                      { value: "viewer", label: "Viewer" },
                      { value: "editor", label: "Editor" },
                      { value: "owner", label: "Owner" },
                      { value: "none", label: "No role yet" },
                    ]}
                  />
                )}
              </Field>
            </div>
          )}
          {err && !emailErr && <Banner tone="error">{err}</Banner>}
        </>
      )}
    </Dialog>
  );
}

function ResetDialog({ person, onClose }: { person: Person; onClose: () => void }) {
  const client = useApiClient();
  const [password, setPassword] = useState(() => tempPassword());
  const reset = useMutation({
    mutationFn: () =>
      data(
        Users.updateUser({
          client,
          path: { uid: person.id },
          body: { password },
        }),
      ),
  });
  const who = person.name || person.email;
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`Reset ${who}’s password?`}
      description={
        reset.isSuccess
          ? undefined
          : "Their current sessions end, and they sign in with the temporary password below. Their API tokens keep working."
      }
      actions={
        reset.isSuccess ? (
          <Button variant="primary" onClick={onClose}>
            Done
          </Button>
        ) : (
          <>
            <Button variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button
              variant="primary"
              disabled={password.length < 10 || reset.isPending}
              disabledReason={password.length < 10 ? "Use at least 10 characters" : undefined}
              onClick={() => reset.mutate()}
            >
              {reset.isPending ? "Resetting…" : "Reset password"}
            </Button>
          </>
        )
      }
    >
      {reset.isSuccess ? (
        <Done title={`${who} was signed out everywhere.`} password={password} />
      ) : (
        <PasswordField value={password} onChange={setPassword} />
      )}
      {reset.isError && <Banner tone="error">{reset.error.message}</Banner>}
    </Dialog>
  );
}

function DisableDialog({ person, onClose }: { person: Person; onClose: () => void }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const who = person.name || person.email;
  const first = (person.name || person.email).split(/[\s@]/)[0];
  const disable = useMutation({
    mutationFn: () =>
      data(
        Users.updateUser({
          client,
          path: { uid: person.id },
          body: { disabled: true },
        }),
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["users"] });
      toast({
        title: `Disabled ${who}`,
        body: "Re-enable from the same menu to restore everything.",
        tone: "green",
      });
      onClose();
    },
  });
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`Disable ${who}?`}
      description={`${first} is signed out now and can’t sign in. Their API tokens stop working. Their roles, notes and edits are kept, so re-enabling restores everything.`}
      actions={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="danger" disabled={disable.isPending} onClick={() => disable.mutate()}>
            {disable.isPending ? "Disabling…" : "Disable account"}
          </Button>
        </>
      }
    >
      {disable.isError && <Banner tone="error">{disable.error.message}</Banner>}
    </Dialog>
  );
}

function RenameDialog({ person, onClose }: { person: Person; onClose: () => void }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const [name, setName] = useState(person.name ?? "");
  const rename = useMutation({
    mutationFn: () =>
      data(
        Users.updateUser({
          client,
          path: { uid: person.id },
          body: { name: name.trim() },
        }),
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["users"] });
      void qc.invalidateQueries({ queryKey: ["me"] });
      onClose();
    },
  });
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`Rename ${person.name || person.email}`}
      actions={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" disabled={!name.trim() || rename.isPending} onClick={() => rename.mutate()}>
            {rename.isPending ? "Saving…" : "Save"}
          </Button>
        </>
      }
    >
      <Field label="Name">
        {(f) => <Input id={f.id} value={name} onChange={(e) => setName(e.target.value)} maxLength={80} autoFocus />}
      </Field>
      {rename.isError && <Banner tone="error">{rename.error.message}</Banner>}
    </Dialog>
  );
}
