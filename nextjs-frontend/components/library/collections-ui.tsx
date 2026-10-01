"use client";

import { Ellipsis, FolderPlus, FolderTree, Plus } from "lucide-react";
import { useEffect, useState } from "react";

import type { CollectionNode } from "@/app/openapi-client/types.gen";
import {
  collectionPath,
  defaultId,
  holds,
  moveTargets,
  pickerOptions,
  whyNoDelete,
} from "@/components/library/collections-model";
import { useCollectionActions, useCollectionTree } from "@/components/library/use-collections";
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
  const nodes = tree.data ?? [];
  const [to, setTo] = useState<number | null>(null);
  useEffect(() => {
    if (open) setTo(null);
  }, [open]);
  const target = nodes.find((n) => n.id === (to ?? current ?? defaultId(nodes)));
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
          {(f) => <CollectionSelect id={f.id} nodes={nodes} value={to ?? current ?? null} onChange={setTo} />}
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
  const canEdit = can("editor", ns);
  const tree = useCollectionTree(open ? ns : null);
  const nodes = tree.data ?? [];
  const actions = useCollectionActions(ns);
  const [editing, setEditing] = useState<Editing>(null);
  const [text, setText] = useState("");
  const [moveTo, setMoveTo] = useState("");
  const [adding, setAdding] = useState("");
  const reason = canEdit ? undefined : needRole("editor", ns);
  useEffect(() => {
    if (!open) {
      setEditing(null);
      setAdding("");
    }
  }, [open]);
  const start = (e: Editing, initial = "") => {
    setEditing(e);
    setText(initial);
    setMoveTo("top");
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
        <Button variant="ghost" onClick={() => onOpenChange(false)}>
          Close
        </Button>
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
              disabled={!canEdit}
              placeholder="Name"
              onChange={(e) => setAdding(e.target.value)}
            />
          )}
        </Field>
        <Button
          type="submit"
          variant="secondary"
          icon={<Plus />}
          disabled={!canEdit || !adding.trim() || actions.create.isPending}
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
                      {!canEdit && (
                        <p className="px-2.5 pb-1.5 pt-1 text-[12px] leading-snug text-fg-muted">{reason}</p>
                      )}
                      <MenuItem disabled={!canEdit} onSelect={() => start({ kind: "rename", id: n.id }, n.name)}>
                        Rename
                      </MenuItem>
                      <MenuItem
                        icon={<FolderPlus />}
                        disabled={!canEdit || (n.depth ?? 0) >= 7}
                        onSelect={() => start({ kind: "inside", id: n.id })}
                      >
                        New collection inside
                      </MenuItem>
                      <MenuItem disabled={!canEdit} onSelect={() => start({ kind: "move", id: n.id })}>
                        Move to…
                      </MenuItem>
                      <MenuItem
                        disabled={!canEdit || Boolean(n.default)}
                        onSelect={() => actions.update.mutate({ cid: n.id, body: { default: true } })}
                      >
                        {n.default ? "This is the default" : "Make it the default"}
                      </MenuItem>
                      <MenuSeparator />
                      <MenuItem
                        danger
                        disabled={!canEdit || Boolean(noDelete)}
                        onSelect={() => start({ kind: "delete", id: n.id })}
                      >
                        {canEdit && noDelete ? noDelete : "Delete…"}
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
                        { value: "top", label: `The top of ${ns}` },
                        ...pickerOptions(moveTargets(nodes, n.id)),
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
      <p className={cn("text-[12.5px] leading-snug text-fg-muted", !canEdit && "text-fg-secondary")}>
        {canEdit
          ? `${plural(nodes.length, "collection")}. Only empty collections can be deleted, and never the default.`
          : reason}
      </p>
    </Dialog>
  );
}
