"use client";

import { Ellipsis, Globe, Lock, Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import type { FieldDef } from "@/app/openapi-client/types.gen";
import {
  TARGETS,
  TARGET_LABEL,
  TYPES,
  TYPE_LABEL,
  definedHere,
  definitionProblem,
  isChoice,
  parseOptions,
  whereDefined,
  type FieldTarget,
  type FieldType,
} from "@/components/fields/fields-model";
import { useFieldActions, useFieldUses, useNamespaceFields } from "@/components/fields/use-fields";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Checkbox, Field, Input, Select, Textarea } from "@/components/ui/field";
import { Menu, MenuContent, MenuItem, MenuSeparator, MenuTrigger } from "@/components/ui/menu";
import { Label } from "@/components/ui/panel";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { plural } from "@/lib/format";
import { needRole } from "@/lib/hooks/session";

type Draft = { label: string; type: FieldType; target: FieldTarget; options: string; help: string; published: boolean };
const EMPTY: Draft = { label: "", type: "text", target: "resource", options: "", help: "", published: false };

/**
 * The custom fields defined on a namespace (`collection` null) or on one of its collections: add, rename, change
 * their options, help and whether they're published, and delete them (with their values). Fields defined above, which
 * also apply here, are listed to read.
 */
export function FieldsDialog({
  ns,
  collection = null,
  collectionName,
  canDefine,
  open,
  onOpenChange,
}: {
  ns: string;
  collection?: number | null;
  collectionName?: string;
  /** Whether you may define fields here (editors of the namespace; of the collection, for a collection). */
  canDefine: boolean;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const fields = useNamespaceFields(ns, open);
  const all = fields.data ?? [];
  const here = definedHere(all, collection);
  const place = collection == null ? ns : (collectionName ?? "this collection");
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<FieldDef | null>(null);
  const [deleting, setDeleting] = useState<FieldDef | null>(null);
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      wide
      title={collection == null ? `Fields of ${ns}` : `Fields of ${place}`}
      description={
        collection == null
          ? "Fields defined here describe the resources, collections or files anywhere in the namespace. Published fields show on public pages and in IIIF."
          : `Fields defined here describe what is inside ${place}: its resources, the collections in it, or their files.`
      }
    >
      <div className="flex flex-col gap-4">
        {fields.isLoading ? (
          <Skeleton className="h-24 w-full" />
        ) : here.length === 0 && !adding ? (
          <EmptyState icon={<Plus />} title="No fields here yet" className="py-6">
            {canDefine
              ? "Add one for what your catalogue needs: a series, an interviewer, a format…"
              : needRole("editor", ns)}
          </EmptyState>
        ) : (
          <ul aria-label={`Fields of ${place}`} className="flex flex-col">
            {here.map((f) => (
              <li key={f.id} className="flex items-center gap-2.5 border-t border-border py-2.5 first:border-t-0">
                <div className="min-w-0 flex-1">
                  <p className="flex items-center gap-1.5 truncate text-[14px] font-semibold text-fg">
                    {f.label}
                    {f.published ? (
                      <Badge tone="green">
                        <Globe className="size-3" aria-hidden />
                        Published
                      </Badge>
                    ) : (
                      <Badge>
                        <Lock className="size-3" aria-hidden />
                        Internal
                      </Badge>
                    )}
                  </p>
                  <p className="truncate text-[12.5px] text-fg-muted">
                    {[TARGET_LABEL[f.target], TYPE_LABEL[f.type], f.options?.length ? f.options.join(", ") : null]
                      .filter(Boolean)
                      .join(" · ")}
                  </p>
                </div>
                <Menu>
                  <MenuTrigger asChild>
                    <button
                      type="button"
                      aria-label={`Actions for ${f.label}`}
                      className="grid size-7 shrink-0 place-items-center rounded-full text-fg-secondary hover:bg-surface-neutral"
                    >
                      <Ellipsis className="size-4" />
                    </button>
                  </MenuTrigger>
                  <MenuContent align="end" className="min-w-[200px]">
                    <MenuItem icon={<Pencil />} disabled={!f.can_change} onSelect={() => setEditing(f)}>
                      {f.can_change ? "Edit…" : needRole("editor", ns)}
                    </MenuItem>
                    <MenuSeparator />
                    <MenuItem icon={<Trash2 />} danger disabled={!f.can_change} onSelect={() => setDeleting(f)}>
                      {f.can_change ? "Delete…" : needRole("editor", ns)}
                    </MenuItem>
                  </MenuContent>
                </Menu>
              </li>
            ))}
          </ul>
        )}
        {adding ? (
          <NewField ns={ns} collection={collection} onDone={() => setAdding(false)} />
        ) : (
          <Button
            variant="secondary"
            icon={<Plus />}
            className="self-start"
            disabled={!canDefine}
            disabledReason={canDefine ? undefined : needRole("editor", ns)}
            onClick={() => setAdding(true)}
          >
            Add a field
          </Button>
        )}
        <Inherited all={all} here={collection} ns={ns} />
      </div>
      {editing && <EditField ns={ns} f={editing} onClose={() => setEditing(null)} />}
      {deleting && <DeleteField ns={ns} f={deleting} onClose={() => setDeleting(null)} />}
    </Dialog>
  );
}

/** For a collection: the namespace's own fields, which apply here too. */
function Inherited({ all, here, ns }: { all: FieldDef[]; here: number | null; ns: string }) {
  const others = here == null ? [] : all.filter((f) => f.collection == null);
  if (!others.length) return null;
  return (
    <section aria-label="Defined on the namespace" className="flex flex-col gap-1.5">
      <Label as="h3">Also here, from {ns}</Label>
      <ul className="flex flex-col gap-1">
        {others.map((f) => (
          <li key={f.id} className="truncate text-[13px] text-fg-secondary">
            <b className="font-semibold text-fg">{f.label}</b> · {TARGET_LABEL[f.target]} · {whereDefined(f, ns)}
          </li>
        ))}
      </ul>
    </section>
  );
}

function NewField({ ns, collection, onDone }: { ns: string; collection: number | null; onDone: () => void }) {
  const { create } = useFieldActions(ns);
  const [d, setD] = useState<Draft>(EMPTY);
  const problem = definitionProblem(d);
  const save = () => {
    if (problem) return;
    create.mutate(
      {
        label: d.label.trim(),
        type: d.type,
        target: d.target,
        collection,
        options: isChoice(d.type) ? parseOptions(d.options) : null,
        help: d.help.trim() || null,
        published: d.published,
      },
      { onSuccess: onDone },
    );
  };
  return (
    <form
      aria-label="New field"
      className="flex flex-col gap-3 rounded-lg border border-border bg-surface p-3.5"
      onSubmit={(e) => {
        e.preventDefault();
        save();
      }}
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Name">
          {(ids) => (
            <Input
              id={ids.id}
              value={d.label}
              maxLength={80}
              autoFocus
              onChange={(e) => setD({ ...d, label: e.target.value })}
            />
          )}
        </Field>
        <Field label="It describes">
          {(ids) => (
            <Select
              id={ids.id}
              value={d.target}
              onChange={(e) => setD({ ...d, target: e.target.value as FieldTarget })}
              options={TARGETS.map((t) => ({ value: t.value, label: t.label }))}
            />
          )}
        </Field>
        <Field label="Type">
          {(ids) => (
            <Select
              id={ids.id}
              value={d.type}
              onChange={(e) => setD({ ...d, type: e.target.value as FieldType })}
              options={TYPES.map((t) => ({ value: t.value, label: t.label }))}
            />
          )}
        </Field>
        <Field label="Help" optional>
          {(ids) => (
            <Input id={ids.id} value={d.help} maxLength={300} onChange={(e) => setD({ ...d, help: e.target.value })} />
          )}
        </Field>
      </div>
      {isChoice(d.type) && (
        <Field label="Options" hint="One per line">
          {(ids) => (
            <Textarea
              id={ids.id}
              aria-describedby={ids.describedBy}
              rows={3}
              value={d.options}
              onChange={(e) => setD({ ...d, options: e.target.value })}
            />
          )}
        </Field>
      )}
      <Checkbox
        checked={d.published}
        onCheckedChange={(v) => setD({ ...d, published: v })}
        label="Publish it: show it on public pages and in IIIF"
      />
      <div className="flex justify-end gap-2">
        <Button type="button" size="sm" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
        <Button
          type="submit"
          size="sm"
          variant="primary"
          disabled={Boolean(problem) || create.isPending}
          disabledReason={problem ?? undefined}
        >
          {create.isPending ? "Adding…" : "Add field"}
        </Button>
      </div>
    </form>
  );
}

function EditField({ ns, f, onClose }: { ns: string; f: FieldDef; onClose: () => void }) {
  const { update } = useFieldActions(ns);
  const [label, setLabel] = useState(f.label);
  const [options, setOptions] = useState((f.options ?? []).join("\n"));
  const [help, setHelp] = useState(f.help ?? "");
  const [published, setPublished] = useState(f.published);
  const problem = definitionProblem({ label, type: f.type, options });
  const body = {
    ...(label.trim() !== f.label ? { label: label.trim() } : {}),
    ...(isChoice(f.type) && parseOptions(options).join("\n") !== (f.options ?? []).join("\n")
      ? { options: parseOptions(options) }
      : {}),
    ...((help.trim() || null) !== (f.help ?? null) ? { help: help.trim() || null } : {}),
    ...(published !== f.published ? { published } : {}),
  };
  const empty = Object.keys(body).length === 0;
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`Edit ${f.label}`}
      description={`${TARGET_LABEL[f.target]} · ${TYPE_LABEL[f.type]}. A field keeps its type and what it describes.`}
      actions={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={empty || Boolean(problem) || update.isPending}
            disabledReason={problem ?? (empty ? "Nothing has changed" : undefined)}
            onClick={() => update.mutate({ fid: f.id, body }, { onSuccess: onClose })}
          >
            {update.isPending ? "Saving…" : "Save"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <Field label="Name">
          {(ids) => <Input id={ids.id} value={label} maxLength={80} onChange={(e) => setLabel(e.target.value)} />}
        </Field>
        {isChoice(f.type) && (
          <Field label="Options" hint="One per line. An option that items have chosen can't be taken away.">
            {(ids) => (
              <Textarea
                id={ids.id}
                aria-describedby={ids.describedBy}
                rows={4}
                value={options}
                onChange={(e) => setOptions(e.target.value)}
              />
            )}
          </Field>
        )}
        <Field label="Help" optional>
          {(ids) => <Input id={ids.id} value={help} maxLength={300} onChange={(e) => setHelp(e.target.value)} />}
        </Field>
        <Checkbox
          checked={published}
          onCheckedChange={setPublished}
          label="Publish it: show it on public pages and in IIIF"
        />
      </div>
    </Dialog>
  );
}

function DeleteField({ ns, f, onClose }: { ns: string; f: FieldDef; onClose: () => void }) {
  const { remove } = useFieldActions(ns);
  const uses = useFieldUses(ns, f.id);
  const n = uses.data?.uses ?? null;
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`Delete ${f.label}?`}
      description={
        n == null
          ? "Counting its values…"
          : n
            ? `${plural(n, "item has", "items have")} a value for it. They go too, and this can't be undone.`
            : "Nothing has a value for it yet."
      }
      actions={
        <>
          <Button variant="ghost" onClick={onClose}>
            Keep it
          </Button>
          <Button
            variant="danger"
            disabled={n == null || remove.isPending}
            onClick={() => remove.mutate(f.id, { onSuccess: onClose })}
          >
            {remove.isPending ? "Deleting…" : n ? `Delete it and ${plural(n, "value")}` : "Delete it"}
          </Button>
        </>
      }
    />
  );
}
