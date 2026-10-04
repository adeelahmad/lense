"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Entities } from "@/app/openapi-client";
import type { EntityType } from "@/app/openapi-client/types.gen";
import { splitAliases } from "@/components/entities/model";
import { useCollectionTree } from "@/components/library/use-collections";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select, Textarea } from "@/components/ui/field";
import { ApiError, data, useApiClient } from "@/lib/api/browser";

/** Add an entity to the fixed list of a namespace, or of one collection and those inside it. */
export function DefineDialog({
  ns,
  types,
  open,
  onOpenChange,
  onDefined,
}: {
  ns: string;
  types: EntityType[];
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onDefined: (id: number) => void;
}) {
  const client = useApiClient();
  const qc = useQueryClient();
  const tree = useCollectionTree(open ? ns : null);
  const [name, setName] = useState("");
  const [type, setType] = useState("ORG");
  const [aliases, setAliases] = useState("");
  const [description, setDescription] = useState("");
  const [collection, setCollection] = useState("");
  const define = useMutation({
    mutationFn: () =>
      data(
        Entities.defineEntity({
          client,
          path: { name: ns },
          body: {
            name: name.trim(),
            type,
            aliases: splitAliases(aliases),
            description: description.trim() || null,
            collection: collection ? Number(collection) : null,
          },
        }),
      ),
    onSuccess: (e) => {
      qc.invalidateQueries({ queryKey: ["entities"] });
      setName("");
      setAliases("");
      setDescription("");
      onDefined(e.id);
    },
  });
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Add entity"
      description="Namespaces and collections with a fixed list map the names they find onto these."
      actions={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="primary" form="define-entity" type="submit" disabled={!name.trim() || define.isPending}>
            Add entity
          </Button>
        </>
      }
    >
      <form
        id="define-entity"
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          define.mutate();
        }}
      >
        <Field label="Name">
          {(ids) => (
            <Input id={ids.id} value={name} required maxLength={200} onChange={(e) => setName(e.target.value)} />
          )}
        </Field>
        <Field label="Type">
          {(ids) => (
            <Select
              id={ids.id}
              value={type}
              onChange={(e) => setType(e.target.value)}
              options={types.filter((t) => !t.quiet).map((t) => ({ value: t.type, label: t.label }))}
            />
          )}
        </Field>
        <Field label="Also said as" optional hint="Other names, spellings or abbreviations, separated by commas.">
          {(ids) => (
            <Input
              id={ids.id}
              aria-describedby={ids.describedBy}
              value={aliases}
              placeholder="e.g. Acme, ACME Inc"
              onChange={(e) => setAliases(e.target.value)}
            />
          )}
        </Field>
        <Field label="Description" optional>
          {(ids) => (
            <Textarea
              id={ids.id}
              rows={3}
              maxLength={2000}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          )}
        </Field>
        <Field label="For" hint="An entity of one collection is only matched in its recordings and those inside it.">
          {(ids) => (
            <Select
              id={ids.id}
              aria-describedby={ids.describedBy}
              value={collection}
              onChange={(e) => setCollection(e.target.value)}
              options={[
                { value: "", label: `All of ${ns}` },
                ...(tree.data ?? []).map((c) => ({ value: String(c.id), label: (c.path ?? [c.name]).join(" / ") })),
              ]}
            />
          )}
        </Field>
        {define.isError && (
          <p role="alert" className="m-0 text-[13px] text-red-dark">
            {define.error instanceof ApiError ? define.error.message : "Couldn’t add it. Please try again."}
          </p>
        )}
      </form>
    </Dialog>
  );
}
