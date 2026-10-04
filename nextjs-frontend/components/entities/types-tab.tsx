"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Entities } from "@/app/openapi-client";
import type { EntityTypeInfo } from "@/app/openapi-client/types.gen";
import { setupKey, useEntitySetup } from "@/components/entities/setup-tab";
import { Badge } from "@/components/ui/badge";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Field, Input, Textarea } from "@/components/ui/field";
import { Panel } from "@/components/ui/panel";
import { SkeletonRows } from "@/components/ui/states";
import { Table, Td, Th, THead, Tr } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { needRole } from "@/lib/hooks/session";

function TypeForm({ ns, type, onDone }: { ns: string; type?: EntityTypeInfo; onDone: () => void }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const [label, setLabel] = useState(type?.label ?? "");
  const [description, setDescription] = useState(type?.description ?? "");
  const save = useMutation({
    mutationFn: () =>
      type
        ? data(Entities.updateEntityType({ client, path: { name: ns, code: type.type }, body: { label, description } }))
        : data(Entities.createEntityType({ client, path: { name: ns }, body: { label, description } })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: setupKey(ns) });
      qc.invalidateQueries({ queryKey: ["entity-types"] });
      onDone();
    },
    onError: (e) =>
      toast({ title: "Couldn’t save the type", body: e instanceof ApiError ? e.message : "", tone: "red" }),
  });
  return (
    <form
      className="flex flex-col gap-3 rounded-md border border-border p-3"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <Field label="Name" hint={type ? `Code ${type.type}` : "e.g. Client, Project, Drug"}>
        {(ids) => (
          <Input id={ids.id} value={label} maxLength={40} required onChange={(e) => setLabel(e.target.value)} />
        )}
      </Field>
      <Field label="What counts as one" optional>
        {(ids) => (
          <Textarea
            id={ids.id}
            rows={2}
            maxLength={2000}
            value={description}
            placeholder="e.g. A company that pays us for a project"
            onChange={(e) => setDescription(e.target.value)}
          />
        )}
      </Field>
      <div className="flex gap-2">
        <Button type="submit" size="sm" variant="primary" disabled={!label.trim() || save.isPending}>
          {type ? "Save" : "Add type"}
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

/** The types entities of a namespace may have: Lens's own, and the namespace's. */
export function TypesTab({ ns }: { ns: string }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const view = useEntitySetup(ns);
  const [editing, setEditing] = useState<string | "new" | null>(null);
  const remove = useMutation({
    mutationFn: (code: string) => data(Entities.deleteEntityType({ client, path: { name: ns, code } })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: setupKey(ns) });
      qc.invalidateQueries({ queryKey: ["entity-types"] });
    },
    onError: (e) =>
      toast({ title: "Couldn’t delete the type", body: e instanceof ApiError ? e.message : "", tone: "red" }),
  });
  if (view.isLoading) return <SkeletonRows rows={6} />;
  if (view.isError) return <Banner tone="error">{view.error.message}</Banner>;
  const can = view.data!.can_change;
  const types = view.data!.types ?? [];
  return (
    <Panel
      className="max-w-[860px]"
      title="Entity types"
      subtitle="Add types of your own for what matters in this namespace; extractors and editors can use them."
      actions={
        editing !== "new" && (
          <Button
            size="sm"
            disabled={!can}
            disabledReason={!can ? needRole("editor", ns) : undefined}
            onClick={() => setEditing("new")}
          >
            Add a type
          </Button>
        )
      }
    >
      <div className="flex flex-col gap-3">
        {editing === "new" && <TypeForm ns={ns} onDone={() => setEditing(null)} />}
        <div className="overflow-hidden rounded-md border border-border">
          <Table aria-label="Entity types">
            <THead className="border-t-0">
              <tr>
                <Th className="w-[200px]">Type</Th>
                <Th>What counts as one</Th>
                <Th className="w-[160px]">
                  <span className="sr-only">Actions</span>
                </Th>
              </tr>
            </THead>
            <tbody>
              {types.map((t) =>
                editing === t.type ? (
                  <tr key={t.type}>
                    <td colSpan={3} className="p-3">
                      <TypeForm ns={ns} type={t} onDone={() => setEditing(null)} />
                    </td>
                  </tr>
                ) : (
                  <Tr key={t.type}>
                    <Td>
                      <span className="flex items-center gap-2">
                        <span className="font-semibold text-fg">{t.label}</span>
                        {t.builtin === false && <Badge tone="intent">own</Badge>}
                      </span>
                    </Td>
                    <Td className="text-[13px] text-fg-secondary">{t.description || "—"}</Td>
                    <Td className="text-right">
                      {t.builtin === false && can && (
                        <span className="inline-flex gap-1">
                          <Button size="xs" variant="ghost" onClick={() => setEditing(t.type)}>
                            Edit
                          </Button>
                          <Button
                            size="xs"
                            variant="danger-ghost"
                            disabled={remove.isPending}
                            onClick={() => remove.mutate(t.type)}
                          >
                            Delete
                          </Button>
                        </span>
                      )}
                    </Td>
                  </Tr>
                ),
              )}
            </tbody>
          </Table>
        </div>
      </div>
    </Panel>
  );
}
