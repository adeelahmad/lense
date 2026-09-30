"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Metadata } from "@/app/openapi-client";
import { ACCESS, LANG_RX } from "@/components/iiif/metadata-model";
import { RIGHTS } from "@/components/iiif/rights";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { count } from "@/lib/format";

/** Above this many recordings, applying needs the count typed out (as the batch double-check does). */
export const TYPED_OVER = 100;

type BulkField = "access" | "rights" | "attribution" | "provider" | "language";
const FIELDS: { value: BulkField; label: string }[] = [
  { value: "access", label: "Access" },
  { value: "rights", label: "Rights" },
  { value: "attribution", label: "Required attribution" },
  { value: "provider", label: "Provider" },
  { value: "language", label: "Languages spoken" },
];

/** The value to send for a field, or an error. */
export function bulkValue(field: BulkField, raw: string): { value: unknown } | { error: string } {
  const v = raw.trim();
  if (!v) return { error: "Choose a value" };
  switch (field) {
    case "provider":
      return { value: { name: v } };
    case "language": {
      const codes = v.split(/[\s,]+/).filter(Boolean);
      return codes.every((c) => LANG_RX.test(c) && c !== "none")
        ? { value: codes }
        : { error: "Use language codes such as en, pt-BR" };
    }
    default:
      return { value: v };
  }
}

/**
 * Set or clear one field on every recording in a namespace (MD4's dry run, backed by /metadata/bulk): a dry run reports
 * how many would change, then applying asks you to type the count above 100 recordings.
 */
export function BulkEditDialog({
  ns,
  open,
  onClose,
  preset,
}: {
  ns: string;
  open: boolean;
  onClose: () => void;
  preset?: { field: BulkField; value: string; title: string };
}) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const [field, setField] = useState<BulkField>(preset?.field ?? "rights");
  const [mode, setMode] = useState<"set" | "clear">("set");
  const [raw, setRaw] = useState(preset?.value ?? "");
  const [typed, setTyped] = useState("");
  const parsed = mode === "clear" ? { value: null } : bulkValue(field, raw);
  const body = () => ({
    namespace: ns,
    set: mode === "set" && "value" in parsed ? { [field]: parsed.value } : {},
    clear: mode === "clear" ? [field] : [],
  });

  const dry = useMutation({
    mutationFn: () =>
      data(
        Metadata.bulkUpdateMetadata({
          client,
          body: { ...body(), dry_run: true },
        }),
      ),
  });
  const apply = useMutation({
    mutationFn: () =>
      data(
        Metadata.bulkUpdateMetadata({
          client,
          body: { ...body(), dry_run: false },
        }),
      ),
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: ["metadata"] });
      void qc.invalidateQueries({ queryKey: ["iiif"] });
      void qc.invalidateQueries({ queryKey: ["iiif-public"] });
      toast({
        title: `${count(r.changed ?? 0)} recording${r.changed === 1 ? "" : "s"} changed`,
        body: `${FIELDS.find((f) => f.value === field)?.label} in ${ns}. Each change is in its recording’s history.`,
        tone: "green",
      });
      close();
    },
  });
  const close = () => {
    dry.reset();
    apply.reset();
    setTyped("");
    onClose();
  };
  const n = dry.data?.would_change ?? 0;
  const needTyped = n > TYPED_OVER;
  const confirmText = `CHANGE ${n}`;

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => !o && close()}
      title={preset?.title ?? `Bulk edit ${ns}`}
      description="Sets one field on every recording in this namespace. Nothing is saved until you apply the dry run."
      actions={
        <>
          <Button variant="ghost" onClick={close}>
            Cancel
          </Button>
          {!dry.data ? (
            <Button
              variant="primary"
              disabled={"error" in parsed || dry.isPending}
              disabledReason={"error" in parsed ? parsed.error : undefined}
              onClick={() => dry.mutate()}
            >
              {dry.isPending ? "Checking…" : "Dry run"}
            </Button>
          ) : (
            <Button
              variant="primary"
              disabled={!n || apply.isPending || (needTyped && typed.trim() !== confirmText)}
              disabledReason={
                !n
                  ? "Nothing would change"
                  : needTyped && typed.trim() !== confirmText
                    ? `Type ${confirmText} to confirm`
                    : undefined
              }
              onClick={() => apply.mutate()}
            >
              {apply.isPending ? "Applying…" : `Apply to ${count(n)}`}
            </Button>
          )}
        </>
      }
    >
      {!preset && (
        <div className="grid gap-3 sm:grid-cols-[1fr_auto]">
          <Field label="Field">
            {(f) => (
              <Select
                id={f.id}
                value={field}
                onChange={(e) => {
                  setField(e.target.value as BulkField);
                  setRaw("");
                  dry.reset();
                }}
                options={FIELDS}
              />
            )}
          </Field>
          <Field label="Change">
            {(f) => (
              <Select
                id={f.id}
                value={mode}
                onChange={(e) => {
                  setMode(e.target.value as "set" | "clear");
                  dry.reset();
                }}
                options={[
                  { value: "set", label: "Set to" },
                  { value: "clear", label: "Clear it" },
                ]}
                className="w-[140px]"
              />
            )}
          </Field>
        </div>
      )}
      {mode === "set" && !preset && (
        <Field
          label="Value"
          hint={
            field === "language"
              ? "Codes separated by commas"
              : field === "provider"
                ? "The organisation’s name"
                : undefined
          }
        >
          {(f) =>
            field === "access" ? (
              <Select
                id={f.id}
                value={raw}
                onChange={(e) => (setRaw(e.target.value), dry.reset())}
                options={[
                  { value: "", label: "Choose…" },
                  ...ACCESS.map((a) => ({
                    value: a.value,
                    label: `${a.label} — ${a.hint}`,
                  })),
                ]}
              />
            ) : field === "rights" ? (
              <Select
                id={f.id}
                value={raw}
                onChange={(e) => (setRaw(e.target.value), dry.reset())}
                options={[
                  { value: "", label: "Choose…" },
                  ...RIGHTS.map((r) => ({
                    value: r.uri,
                    label: `${r.code} — ${r.name}`,
                  })),
                ]}
              />
            ) : (
              <Input
                id={f.id}
                aria-describedby={f.describedBy}
                value={raw}
                onChange={(e) => (setRaw(e.target.value), dry.reset())}
              />
            )
          }
        </Field>
      )}
      {dry.data && (
        <div className="flex flex-col gap-2 rounded-md border border-border p-3">
          <b className="text-[14px] font-bold">Dry run — nothing saved yet</b>
          <div className="tabular flex flex-wrap gap-4 text-[13px] font-medium">
            <span>
              <b>{count(n)}</b> will change
            </span>
            <span>
              <b>{count(dry.data.recordings - n)}</b> unchanged
            </span>
          </div>
          {needTyped && (
            <Field label={`Type ${confirmText} to confirm`}>
              {(f) => (
                <Input id={f.id} value={typed} onChange={(e) => setTyped(e.target.value)} mono autoComplete="off" />
              )}
            </Field>
          )}
        </div>
      )}
      {(dry.isError || apply.isError) && <Banner tone="error">{(dry.error ?? apply.error)?.message}</Banner>}
    </Dialog>
  );
}
