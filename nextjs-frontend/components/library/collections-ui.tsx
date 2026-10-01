"use client";

import { Ellipsis, FolderPlus, FolderTree, Plus, SlidersHorizontal, UsersRound, X } from "lucide-react";
import { useEffect, useState } from "react";

import type { CollectionMember, CollectionNode } from "@/app/openapi-client/types.gen";
import {
  collectionPath,
  defaultId,
  holds,
  moveTargets,
  pickerOptions,
  whyNoDelete,
} from "@/components/library/collections-model";
import {
  useCollectionActions,
  useCollectionMembers,
  useCollectionTree,
  useSetCollectionMember,
} from "@/components/library/use-collections";
import { FieldsDialog } from "@/components/fields/fields-dialog";
import { FieldValuesPanel } from "@/components/fields/fields-ui";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select } from "@/components/ui/field";
import { Menu, MenuContent, MenuItem, MenuSeparator, MenuTrigger } from "@/components/ui/menu";
import { EmptyState, SkeletonRows } from "@/components/ui/states";
import { plural } from "@/lib/format";
import { needRole, useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

/** Pick one of a namespace's collections (indented by depth; the default marked). */
export function CollectionSelect({
  nodes,
  value,
  onChange,
  id,
  disabled,
  "aria-label": ariaLabel,
}: {
  nodes: CollectionNode[];
  value: number | null;
  onChange: (id: number) => void;
  id?: string;
  disabled?: boolean;
  "aria-label"?: string;
}) {
  const current = value ?? defaultId(nodes);
  return (
    <Select
      id={id}
      aria-label={ariaLabel}
      disabled={disabled || !nodes.length}
      options={pickerOptions(nodes)}
      value={current != null ? String(current) : ""}
      onChange={(e) => onChange(Number(e.target.value))}
    />
  );
}

/** Which collection of a namespace new recordings go into: its default unless another is picked. A namespace that
 * doesn't exist yet (an admin's new one) starts with one collection, General. */
export function CollectionField({
  ns,
  value,
  onChange,
  id = "collection-field",
}: {
  ns: string | null;
  value: number | null;
  onChange: (id: number | null) => void;
  id?: string;
}) {
  const { namespaces } = useArchive();
  const known = Boolean(ns && namespaces.some((n) => n.name === ns));
  const tree = useCollectionTree(known ? ns : null);
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-[13px] font-bold text-fg-strong">
        Collection
      </label>
      {known ? (
        <CollectionSelect id={id} nodes={tree.data ?? []} value={value} onChange={onChange} disabled={tree.isLoading} />
      ) : (
        <Select
          id={id}
          disabled
          value=""
          options={[{ value: "", label: ns ? "General" : "Choose a namespace first" }]}
        />
      )}
      {!known && ns && (
        <span className="text-[12px] leading-snug text-fg-muted">
          A new namespace starts with one collection, General.
        </span>
      )}
    </div>
  );
}

/** Move recordings of one namespace into one of its collections. */
export function PlaceDialog({
  ns,
  ids,
  title,
  current,
  open,
  onOpenChange,
  onDone,
}: {
  ns: string;
  ids: number[];
  /** What the dialog moves: a recording's title, or "3 recordings". */
  title: string;
  /** The collection they're in now, when they share one. */
  current?: number | null;
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onDone?: () => void;
}) {
  const tree = useCollectionTree(open ? ns : null);
  const { place } = useCollectionActions(ns);
  // where they may put recordings: collections they edit (through the namespace, or a role on the collection)
  const nodes = (tree.data ?? []).filter((n) => n.role === "editor" || n.role === "admin" || n.id === current);
  const [to, setTo] = useState<number | null>(null);
  useEffect(() => {
    if (open) setTo(null);
  }, [open]);
  const chosen = to ?? current ?? defaultId(nodes) ?? nodes[0]?.id ?? null;
  const target = nodes.find((n) => n.id === chosen);
  const same = target != null && target.id === current;
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => !place.isPending && onOpenChange(o)}
      title={`Move ${title} to a collection`}
      description={`Collections of ${ns}. Recordings live in one collection; IIIF lists them there.`}
      actions={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={!target || same || place.isPending}
            disabledReason={same ? `Already in “${target?.name}”` : !target ? "Pick a collection" : undefined}
            onClick={() =>
              target &&
              place.mutate(
                { recordings: ids, collection: target.id, name: target.name },
                {
                  onSuccess: () => {
                    onOpenChange(false);
                    onDone?.();
                  },
                },
              )
            }
          >
            {place.isPending ? "Moving…" : "Move"}
          </Button>
        </>
      }
    >
      {tree.isLoading ? (
        <SkeletonRows rows={2} />
      ) : (
        <Field label="Collection">
          {(f) => <CollectionSelect id={f.id} nodes={nodes} value={chosen} onChange={setTo} />}
        </Field>
      )}
    </Dialog>
  );
}

type Editing =
  | { kind: "rename"; id: number }
  | { kind: "inside"; id: number }
  | { kind: "move"; id: number }
  | { kind: "delete"; id: number }
  | null;

/** A namespace's collections, to arrange: make (at the top or inside one), rename, move, make default, delete. */
export function CollectionsDialog({
  ns,
  open,
  onOpenChange,
  onPick,
}: {
  ns: string;
  open: boolean;
  onOpenChange: (o: boolean) => void;
  /** Show one in the Library. */
  onPick?: (id: number) => void;
}) {
  const { can } = useArchive();
  // editors of the namespace arrange every collection (and the top, and the default); an admin of a collection, it
  // and the ones inside it; owners and admins of a collection give roles on it (each node says what this person may)
  const canTop = can("editor", ns);
  const tree = useCollectionTree(open ? ns : null);
  const nodes = tree.data ?? [];
  const actions = useCollectionActions(ns);
  const [editing, setEditing] = useState<Editing>(null);
  const [text, setText] = useState("");
  const [moveTo, setMoveTo] = useState("");
  const [adding, setAdding] = useState("");
  const [people, setPeople] = useState<CollectionNode | null>(null);
  // custom fields: defined on the namespace or on a collection; a collection's own values
  const [fieldsOf, setFieldsOf] = useState<CollectionNode | "namespace" | null>(null);
  const [describing, setDescribing] = useState<CollectionNode | null>(null);
  const reason = canTop ? undefined : needRole("editor", ns);
  const noChange = `Editors of ${ns}, or admins of this collection, can do this`;
  useEffect(() => {
    if (!open) {
      setEditing(null);
      setAdding("");
    }
  }, [open]);
  const targets = (id: number) => moveTargets(nodes, id).filter((t) => t.can_change);
  const start = (e: Editing, initial = "") => {
    setEditing(e);
    setText(initial);
    setMoveTo(canTop || !e ? "top" : String(targets(e.id)[0]?.id ?? ""));
  };
  const done = { onSuccess: () => setEditing(null) };

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={`Collections in ${ns}`}
      description="Every recording lives in one collection. New recordings go to the default unless someone picks another."
      className="max-w-[640px]"
      actions={
        <>
          <Button
            variant="ghost"
            icon={<SlidersHorizontal />}
            className="mr-auto"
            onClick={() => setFieldsOf("namespace")}
          >
            Fields of {ns}…
          </Button>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Close
          </Button>
        </>
      }
    >
      <form
        className="flex items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (adding.trim()) actions.create.mutate({ name: adding.trim() }, { onSuccess: () => setAdding("") });
        }}
      >
        <Field label="New collection" className="min-w-0 flex-1">
          {(f) => (
            <Input
              id={f.id}
              value={adding}
              maxLength={120}
              disabled={!canTop}
              placeholder="Name"
              onChange={(e) => setAdding(e.target.value)}
            />
          )}
        </Field>
        <Button
          type="submit"
          variant="secondary"
          icon={<Plus />}
          disabled={!canTop || !adding.trim() || actions.create.isPending}
          disabledReason={reason}
        >
          Add
        </Button>
      </form>
      {tree.isLoading ? (
        <SkeletonRows rows={3} />
      ) : !nodes.length ? (
        <EmptyState icon={<FolderTree />} title="No collections" className="py-6" />
      ) : (
        <ul aria-label={`Collections in ${ns}`} className="m-0 flex list-none flex-col p-0">
          {nodes.map((n) => {
            const noDelete = whyNoDelete(n);
            const here = editing?.id === n.id ? editing : null;
            const change = Boolean(n.can_change);
            return (
              <li
                key={n.id}
                className="flex flex-col gap-1.5 border-t border-border py-2 first:border-t-0"
                style={{ paddingLeft: Math.min(n.depth ?? 0, 6) * 18 }}
              >
                <div className="flex min-w-0 items-center gap-2">
                  <FolderTree aria-hidden className="size-4 shrink-0 text-fg-muted" />
                  {here?.kind === "rename" ? (
                    <form
                      className="flex min-w-0 flex-1 items-center gap-1.5"
                      onSubmit={(e) => {
                        e.preventDefault();
                        if (text.trim()) actions.update.mutate({ cid: n.id, body: { name: text.trim() } }, done);
                      }}
                    >
                      <Input
                        aria-label={`New name for ${n.name}`}
                        value={text}
                        autoFocus
                        maxLength={120}
                        onChange={(e) => setText(e.target.value)}
                        className="h-8"
                      />
                      <Button
                        type="submit"
                        size="xs"
                        variant="primary"
                        disabled={!text.trim() || actions.update.isPending}
                      >
                        Save
                      </Button>
                      <Button type="button" size="xs" variant="ghost" onClick={() => setEditing(null)}>
                        Cancel
                      </Button>
                    </form>
                  ) : (
                    <button
                      type="button"
                      className="min-w-0 flex-1 truncate text-left text-[14px] font-semibold text-fg hover:underline disabled:no-underline"
                      disabled={!onPick}
                      onClick={() => {
                        onPick?.(n.id);
                        onOpenChange(false);
                      }}
                      title={onPick ? `Show ${collectionPath(n)} in the Library` : undefined}
                    >
                      {n.name}
                      {n.default && (
                        <span className="ml-2 rounded-pill border border-border px-1.5 py-0.5 align-[1px] text-[11px] font-semibold text-fg-secondary">
                          Default
                        </span>
                      )}
                    </button>
                  )}
                  <span className="hidden shrink-0 text-[12px] text-fg-muted sm:inline">{holds(n)}</span>
                  <Menu>
                    <MenuTrigger asChild>
                      <button
                        type="button"
                        aria-label={`Actions for ${n.name}`}
                        className="grid size-7 shrink-0 place-items-center rounded-full text-fg-secondary hover:bg-surface-neutral"
                      >
                        <Ellipsis className="size-4" />
                      </button>
                    </MenuTrigger>
                    <MenuContent align="end" className="min-w-[230px]">
                      {!change && (
                        <p className="px-2.5 pb-1.5 pt-1 text-[12px] leading-snug text-fg-muted">{noChange}</p>
                      )}
                      <MenuItem disabled={!change} onSelect={() => start({ kind: "rename", id: n.id }, n.name)}>
                        Rename
                      </MenuItem>
                      <MenuItem
                        icon={<FolderPlus />}
                        disabled={!change || (n.depth ?? 0) >= 7}
                        onSelect={() => start({ kind: "inside", id: n.id })}
                      >
                        New collection inside
                      </MenuItem>
                      <MenuItem
                        disabled={!change || (!canTop && !targets(n.id).length)}
                        onSelect={() => start({ kind: "move", id: n.id })}
                      >
                        Move to…
                      </MenuItem>
                      <MenuItem
                        disabled={!canTop || Boolean(n.default)}
                        onSelect={() => actions.update.mutate({ cid: n.id, body: { default: true } })}
                      >
                        {n.default ? "This is the default" : "Make it the default"}
                      </MenuItem>
                      <MenuItem icon={<UsersRound />} disabled={!n.can_grant} onSelect={() => setPeople(n)}>
                        People…
                      </MenuItem>
                      <MenuItem icon={<SlidersHorizontal />} onSelect={() => setFieldsOf(n)}>
                        Fields…
                      </MenuItem>
                      <MenuItem onSelect={() => setDescribing(n)}>Describe…</MenuItem>
                      <MenuSeparator />
                      <MenuItem
                        danger
                        disabled={!change || Boolean(noDelete)}
                        onSelect={() => start({ kind: "delete", id: n.id })}
                      >
                        {change && noDelete ? noDelete : "Delete…"}
                      </MenuItem>
                    </MenuContent>
                  </Menu>
                </div>
                <span className="pl-6 text-[12px] text-fg-muted sm:hidden">{holds(n)}</span>
                {here?.kind === "inside" && (
                  <form
                    className="flex items-center gap-1.5 pl-6"
                    onSubmit={(e) => {
                      e.preventDefault();
                      if (text.trim()) actions.create.mutate({ name: text.trim(), parent: n.id }, done);
                    }}
                  >
                    <Input
                      aria-label={`New collection inside ${n.name}`}
                      value={text}
                      autoFocus
                      maxLength={120}
                      placeholder="Name"
                      onChange={(e) => setText(e.target.value)}
                      className="h-8"
                    />
                    <Button
                      type="submit"
                      size="xs"
                      variant="primary"
                      disabled={!text.trim() || actions.create.isPending}
                    >
                      Add
                    </Button>
                    <Button type="button" size="xs" variant="ghost" onClick={() => setEditing(null)}>
                      Cancel
                    </Button>
                  </form>
                )}
                {here?.kind === "move" && (
                  <form
                    className="flex flex-wrap items-center gap-1.5 pl-6"
                    onSubmit={(e) => {
                      e.preventDefault();
                      actions.update.mutate(
                        { cid: n.id, body: { parent: moveTo === "top" ? null : Number(moveTo) } },
                        done,
                      );
                    }}
                  >
                    <Select
                      aria-label={`Where to move ${n.name}`}
                      size="sm"
                      className="w-auto min-w-[200px]"
                      value={moveTo}
                      onChange={(e) => setMoveTo(e.target.value)}
                      options={[
                        ...(canTop ? [{ value: "top", label: `The top of ${ns}` }] : []),
                        ...pickerOptions(targets(n.id)),
                      ]}
                    />
                    <Button type="submit" size="xs" variant="primary" disabled={actions.update.isPending}>
                      Move
                    </Button>
                    <Button type="button" size="xs" variant="ghost" onClick={() => setEditing(null)}>
                      Cancel
                    </Button>
                  </form>
                )}
                {here?.kind === "delete" && (
                  <div role="alert" className="flex flex-wrap items-center gap-1.5 pl-6">
                    <span className="flex-1 text-[12.5px] text-fg-strong">Delete “{n.name}”?</span>
                    <Button size="xs" variant="ghost" onClick={() => setEditing(null)}>
                      Keep
                    </Button>
                    <Button
                      size="xs"
                      variant="danger"
                      disabled={actions.remove.isPending}
                      onClick={() => actions.remove.mutate({ cid: n.id, name: n.name }, done)}
                    >
                      Delete
                    </Button>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
      <p className={cn("text-[12.5px] leading-snug text-fg-muted", !canTop && "text-fg-secondary")}>
        {canTop
          ? `${plural(nodes.length, "collection")}. Only empty collections can be deleted, and never the default.`
          : nodes.some((n) => n.can_change)
            ? "You arrange the collections you’re an admin of, and the ones inside them."
            : reason}
      </p>
      {fieldsOf && (
        <FieldsDialog
          ns={ns}
          collection={fieldsOf === "namespace" ? null : fieldsOf.id}
          collectionName={fieldsOf === "namespace" ? undefined : fieldsOf.name}
          canDefine={canTop || (fieldsOf !== "namespace" && (fieldsOf.role === "editor" || fieldsOf.role === "admin"))}
          open
          onOpenChange={(o) => !o && setFieldsOf(null)}
        />
      )}
      {describing && (
        <Dialog
          open
          onOpenChange={(o) => !o && setDescribing(null)}
          title={`Describe ${describing.name}`}
          description="The custom fields that describe this collection. Published ones show in IIIF."
        >
          <FieldValuesPanel source={{ kind: "collection", ns, cid: describing.id }} title="Fields" />
          <p className="text-[13px] leading-snug text-fg-muted">
            Fields for collections are defined with Fields of {ns}…, or Fields… on the collection this one is in.
          </p>
        </Dialog>
      )}
      {people && (
        <MembersDialog ns={ns} node={people} open={Boolean(people)} onOpenChange={(o) => !o && setPeople(null)} />
      )}
    </Dialog>
  );
}

const ROLE_HINT: Record<CollectionMember["role"], string> = {
  viewer: "Sees its recordings",
  editor: "Sees and edits its recordings",
  admin: "Runs it: arranges it, gives roles, acts as an owner of its recordings",
};

const ROLE_OPTIONS = [
  { value: "viewer", label: "Viewer" },
  { value: "editor", label: "Editor" },
  { value: "admin", label: "Admin" },
];

/** Who has a role on a collection: give one by email, change it, take it away. Roles hold for the collections
 * inside it too; people with a role in the namespace keep it everywhere. */
export function MembersDialog({
  ns,
  node,
  open,
  onOpenChange,
}: {
  ns: string;
  node: CollectionNode;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const members = useCollectionMembers(ns, open ? node.id : null);
  const set = useSetCollectionMember(ns, node.id);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<CollectionMember["role"]>("viewer");
  const own = (members.data ?? []).filter((m) => !m.inherited_from);
  const above = (members.data ?? []).filter((m) => m.inherited_from);
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={`People in ${node.name}`}
      description={`Roles here hold for the collections inside it too. Someone without a role in ${ns} sees just these collections; people with one keep it everywhere.`}
      className="max-w-[600px]"
      actions={
        <Button variant="ghost" onClick={() => onOpenChange(false)}>
          Close
        </Button>
      }
    >
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (email.trim()) set.mutate({ email: email.trim(), role }, { onSuccess: () => setEmail("") });
        }}
      >
        <Field label="Email" className="min-w-[200px] flex-1">
          {(f) => (
            <Input
              id={f.id}
              type="email"
              value={email}
              placeholder="name@example.org"
              onChange={(e) => setEmail(e.target.value)}
            />
          )}
        </Field>
        <Field label="Role" className="w-[130px]">
          {(f) => (
            <Select
              id={f.id}
              value={role}
              onChange={(e) => setRole(e.target.value as CollectionMember["role"])}
              options={ROLE_OPTIONS}
            />
          )}
        </Field>
        <Button type="submit" variant="secondary" icon={<Plus />} disabled={!email.trim() || set.isPending}>
          Give
        </Button>
      </form>
      <p className="-mt-1 text-[12.5px] text-fg-muted">{ROLE_HINT[role]}.</p>
      {members.isLoading ? (
        <SkeletonRows rows={2} />
      ) : !own.length && !above.length ? (
        <EmptyState icon={<UsersRound />} title="Nobody has a role here yet" className="py-5">
          Only people with a role in {ns} see it.
        </EmptyState>
      ) : (
        <ul aria-label={`People in ${node.name}`} className="m-0 flex list-none flex-col p-0">
          {own.map((m) => (
            <li key={m.account} className="flex items-center gap-2 border-t border-border py-2 first:border-t-0">
              <span className="flex min-w-0 flex-1 flex-col">
                <span className="truncate text-[14px] font-semibold text-fg">{m.name || m.email}</span>
                {m.name && <span className="truncate text-[12px] text-fg-muted">{m.email}</span>}
              </span>
              <Select
                aria-label={`Role of ${m.name || m.email}`}
                size="sm"
                className="w-[110px]"
                value={m.role}
                disabled={set.isPending}
                onChange={(e) => set.mutate({ account: m.account, role: e.target.value as CollectionMember["role"] })}
                options={ROLE_OPTIONS}
              />
              <button
                type="button"
                aria-label={`Take away ${m.name || m.email}’s role`}
                disabled={set.isPending}
                onClick={() => set.mutate({ account: m.account, role: null })}
                className="grid size-8 shrink-0 place-items-center rounded-full text-fg-secondary hover:bg-surface-neutral disabled:opacity-50"
              >
                <X className="size-4" />
              </button>
            </li>
          ))}
          {above.map((m) => (
            <li
              key={`${m.account}-${m.inherited_from?.id}`}
              className="flex items-center gap-2 border-t border-border py-2 first:border-t-0"
            >
              <span className="flex min-w-0 flex-1 flex-col">
                <span className="truncate text-[14px] font-semibold text-fg-strong">{m.name || m.email}</span>
                <span className="truncate text-[12px] text-fg-muted">
                  {m.role === "admin" ? "Admin" : m.role === "editor" ? "Editor" : "Viewer"} through “
                  {m.inherited_from?.name}”
                </span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </Dialog>
  );
}
