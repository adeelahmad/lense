"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { Sources } from "@/app/openapi-client";
import type { Source, Watch } from "@/app/openapi-client/types.gen";
import { STORAGE_NOUN, confirms } from "@/components/sources/source-model";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { plural } from "@/lib/format";

/** SO3: delete a connection and its watched folders, after typing its name. Imported recordings stay. */
export function DeleteConnectionDialog({
  source,
  watches,
  open,
  onOpenChange,
  onDeleted,
}: {
  source: Source;
  watches: Watch[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDeleted?: () => void;
}) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const [typed, setTyped] = useState("");
  useEffect(() => {
    if (open) setTyped("");
  }, [open]);
  const del = useMutation({
    mutationFn: () => data(Sources.deleteSource({ client, path: { sid: source.id } })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["sources"] });
      void qc.invalidateQueries({ queryKey: ["watches"] });
      toast({ title: "Connection deleted", body: source.name });
      onOpenChange(false);
      onDeleted?.();
    },
    onError: (e: Error) =>
      toast({
        tone: "red",
        title: "Couldn’t delete the connection",
        body: e.message,
      }),
  });
  const namespaces = [...new Set(watches.map((w) => w.namespace).filter(Boolean))];
  const n = watches.length;
  // typing the name is asked only when watched folders go with it; otherwise nothing is lost
  const ok = !n || confirms(typed, source.name);
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={`Delete “${source.name}”?`}
      className="max-w-[440px]"
      description={
        <>
          This removes the connection
          {n ? (
            <>
              {" "}
              and its <b className="text-fg">{plural(n, "watched folder")}</b>
            </>
          ) : null}
          . Recordings already imported stay
          {namespaces.length ? ` in ${namespaces.join(", ")}` : ""}. Files in {STORAGE_NOUN[source.type]} aren’t
          touched.
        </>
      }
      actions={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="danger" disabled={!ok || del.isPending} onClick={() => del.mutate()}>
            {del.isPending ? "Deleting…" : "Delete connection"}
          </Button>
        </>
      }
    >
      {n > 0 && (
        <Field label="Type the connection name to confirm">
          {({ id }) => (
            <Input
              id={id}
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              placeholder={source.name}
              autoComplete="off"
              onKeyDown={(e) => {
                if (e.key !== "Enter") return;
                e.preventDefault();
                if (ok) del.mutate();
              }}
            />
          )}
        </Field>
      )}
    </Dialog>
  );
}
