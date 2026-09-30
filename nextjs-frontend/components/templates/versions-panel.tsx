"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Templates } from "@/app/openapi-client";
import type { Template } from "@/app/openapi-client/types.gen";
import { lineDiff, parseUnifiedDiff, trimContext, type DiffRow } from "@/components/templates/template-model";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/states";
import { data, useApiClient } from "@/lib/api/browser";
import { shortDate } from "@/lib/format";
import { cn } from "@/lib/utils";

function Diff({ rows }: { rows: DiffRow[] }) {
  if (!rows.length) return <p className="px-4 py-3 font-sans text-[13px] text-fg-secondary">No changes to the text.</p>;
  return (
    <div role="table" aria-label="Changes" className="font-mono text-[12.5px] leading-[1.75]">
      {rows.map((r, i) =>
        r.kind === "hunk" ? (
          <div key={i} role="row" className="bg-surface px-4 text-[11.5px] text-fg-muted">
            <span role="cell">{r.text}</span>
          </div>
        ) : (
          <div
            key={i}
            role="row"
            className={cn(
              "grid grid-cols-[40px_40px_20px_minmax(0,1fr)]",
              r.sign === "+" ? "bg-green-surface" : r.sign === "-" ? "bg-red-surface" : "",
            )}
          >
            <span role="cell" className="pr-2 text-right text-fg-muted">
              {r.a ?? ""}
            </span>
            <span role="cell" className="pr-2 text-right text-fg-muted">
              {r.b ?? ""}
            </span>
            <span
              role="cell"
              aria-label={r.sign === "+" ? "added" : r.sign === "-" ? "removed" : undefined}
              className={cn(
                "font-bold",
                r.sign === "+" ? "text-green-dark" : r.sign === "-" ? "text-red-dark" : "text-fg-muted",
              )}
            >
              {r.sign === "-" ? "−" : r.sign === " " ? "" : r.sign}
            </span>
            <span role="cell" className="whitespace-pre-wrap break-words pr-3.5 text-fg">
              {r.text}
            </span>
          </div>
        ),
      )}
    </div>
  );
}

/** TP2: the versions (draft, published, older), what changed in each, restore one as a draft, and what uses it. */
export function VersionsPanel({
  template,
  draftBody,
  usedBy,
  onRestore,
  readOnly,
}: {
  template: Template;
  /** The unsaved text, when it differs from the published version. */
  draftBody: string | null;
  usedBy: string[];
  onRestore: (version: number) => void;
  readOnly?: boolean;
}) {
  const client = useApiClient();
  const history = template.history ?? [];
  const [sel, setSel] = useState<number | "draft">(draftBody != null ? "draft" : template.current);
  const n = typeof sel === "number" ? sel : null;
  const prev = n != null ? history.find((h) => h.version < n)?.version : undefined;

  const diff = useQuery({
    queryKey: ["template-diff", template.id, prev, n],
    queryFn: () =>
      data(
        Templates.diffTemplateVersions({
          client,
          path: { tid: template.id },
          query: { a: prev!, b: n! },
          parseAs: "text",
        }),
      ),
    enabled: n != null && prev != null,
    staleTime: Infinity,
  });
  const first = useQuery({
    queryKey: ["template", template.id, n],
    queryFn: () =>
      data(
        Templates.getTemplate({
          client,
          path: { tid: template.id },
          query: { version: n! },
        }),
      ),
    enabled: n != null && prev == null,
    staleTime: Infinity,
  });

  let rows: DiffRow[] | null = null;
  if (sel === "draft" && draftBody != null) rows = trimContext(lineDiff(template.body, draftBody));
  else if (n != null && prev != null && typeof diff.data === "string") rows = parseUnifiedDiff(diff.data);
  else if (n != null && prev == null && first.data)
    rows = first.data.body.split("\n").map((t, i) => ({
      kind: "line" as const,
      sign: " " as const,
      a: i + 1,
      b: i + 1,
      text: t,
    }));

  return (
    <div className="grid min-h-0 flex-1 md:grid-cols-[260px_minmax(0,1fr)]">
      <aside
        aria-label="Versions"
        className="flex flex-col gap-1 border-b border-border bg-surface p-3.5 md:border-b-0 md:border-r"
      >
        <ul className="flex flex-col gap-1">
          {draftBody != null && (
            <li>
              <button
                type="button"
                aria-pressed={sel === "draft"}
                onClick={() => setSel("draft")}
                className={cn(
                  "flex w-full flex-col gap-[3px] rounded-sm p-2.5 text-left",
                  sel === "draft" ? "bg-blue-surface" : "hover:bg-surface-neutral",
                )}
              >
                <span className="flex items-center gap-1.5">
                  <code className="font-mono text-[12.5px] font-semibold text-fg">
                    v{Math.max(...history.map((h) => h.version), template.current) + 1}
                  </code>
                  <span className="text-[11px] font-semibold text-fg-accent">draft</span>
                </span>
                <span className="text-[11.5px] text-fg-muted">you · not saved</span>
              </button>
            </li>
          )}
          {history.map((h) => (
            <li key={h.version}>
              <button
                type="button"
                aria-pressed={sel === h.version}
                onClick={() => setSel(h.version)}
                className={cn(
                  "flex w-full flex-col gap-[3px] rounded-sm p-2.5 text-left",
                  sel === h.version ? "bg-blue-surface" : "hover:bg-surface-neutral",
                )}
              >
                <span className="flex items-center gap-1.5">
                  <code className="font-mono text-[12.5px] font-semibold text-fg">v{h.version}</code>
                  {h.version === template.current && (
                    <span className="text-[11px] font-semibold text-green-dark">published</span>
                  )}
                  {h.version > template.current && (
                    <span className="text-[11px] font-semibold text-fg-accent">draft</span>
                  )}
                </span>
                <span className="text-[11.5px] leading-snug text-fg-muted">
                  {[h.created_by, h.created_at ? shortDate(h.created_at) : null].filter(Boolean).join(" · ")}
                  {h.notes ? ` · ${h.notes}` : ""}
                </span>
              </button>
            </li>
          ))}
        </ul>
        <div className="label-caps mt-2.5 px-2.5 py-1">Used by</div>
        {usedBy.length ? (
          usedBy.map((u) => (
            <span key={u} className="px-2.5 py-1 text-[13px] font-medium leading-snug text-fg">
              {u}
            </span>
          ))
        ) : (
          <span className="px-2.5 py-1 text-[13px] text-fg-secondary">No pipeline uses it yet.</span>
        )}
      </aside>
      <div className="flex min-w-0 flex-col py-3">
        <div className="px-4 pb-2 text-[12.5px] font-semibold text-fg-secondary">
          {sel === "draft"
            ? `Unsaved changes against v${template.current}`
            : prev != null
              ? `What changed: v${prev} → v${n}`
              : `v${n}: the first version`}
        </div>
        {rows ? (
          <Diff rows={rows} />
        ) : diff.error || first.error ? (
          <p className="px-4 text-[13px] text-red-dark">Couldn’t load the changes.</p>
        ) : (
          <Skeleton className="mx-4 h-32" />
        )}
        {n != null && (
          <div className="flex gap-2 px-4 pb-1 pt-3">
            <Button
              size="sm"
              variant="secondary"
              disabled={readOnly}
              disabledReason="Only admins can change templates"
              onClick={() => onRestore(n)}
            >
              Restore v{n} as draft
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
