"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useState } from "react";

import { Imports } from "@/app/openapi-client";
import { canUndoRun, runDetail, runTitle, undoneToast } from "@/components/import/import-runs";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { plural, relative } from "@/lib/format";

const RUNS = ["import-runs"];

/** Undo a whole import run: every asset it made is deleted, then a toast says how many went. */
export function useUndoImport() {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  return useCallback(
    async (run: number) => {
      try {
        const r = await data(Imports.undoImportRun({ client, path: { run_id: run } }));
        toast({ ...undoneToast(r), tone: "green" });
      } catch (e) {
        toast({ title: "Couldn’t undo the import", body: (e as Error).message, tone: "red" });
      } finally {
        void qc.invalidateQueries({ queryKey: RUNS });
        void qc.invalidateQueries({ queryKey: ["recordings"] });
        void qc.invalidateQueries({ queryKey: ["jobs"] });
      }
    },
    [client, qc, toast],
  );
}

/** The toast right after a bulk import: how many came in, with Undo for the whole run. */
export function useImportedToast() {
  const toast = useToast();
  const qc = useQueryClient();
  const undo = useUndoImport();
  return useCallback(
    (run: number | null | undefined, title: string, body?: string) => {
      void qc.invalidateQueries({ queryKey: RUNS });
      toast({
        title,
        body,
        tone: "green",
        duration: run ? 10000 : undefined,
        action: run ? { label: "Undo", onClick: () => void undo(run) } : undefined,
      });
    },
    [qc, toast, undo],
  );
}

/** Recent bulk imports (folder scans, archives, link lists, a source's files), each with Undo for the whole run. */
export function RecentImports() {
  const client = useApiClient();
  const undo = useUndoImport();
  const [asking, setAsking] = useState<number | null>(null);
  const runs = useQuery({
    queryKey: RUNS,
    queryFn: () => data(Imports.listImportRuns({ client, query: { limit: 10 } })),
    staleTime: 10_000,
  });
  if (!runs.data?.length) return null;
  return (
    <section aria-label="Recent imports" className="flex flex-col gap-2 border-t border-border px-4 py-4 md:px-6">
      <h2 className="text-[14px] font-bold text-fg-strong">Recent imports</h2>
      <ul className="flex flex-col">
        {runs.data.map((r) => (
          <li key={r.id} className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 border-t border-border py-2">
            <div className="min-w-0 flex-1">
              <div className="text-[13.5px] font-semibold text-fg">{runTitle(r)}</div>
              <div className="truncate text-[12.5px] text-fg-secondary" title={r.source ?? undefined}>
                {runDetail(r)} · {relative(r.created_at)}
              </div>
            </div>
            {canUndoRun(r) &&
              (asking === r.id ? (
                <span className="flex items-center gap-2">
                  <Button
                    size="sm"
                    variant="danger"
                    autoFocus
                    onClick={() => {
                      setAsking(null);
                      void undo(r.id);
                    }}
                  >
                    Delete {plural(r.left ?? 0, "asset")}
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setAsking(null)}>
                    Keep
                  </Button>
                </span>
              ) : (
                <Button size="sm" variant="secondary" onClick={() => setAsking(r.id)}>
                  Undo
                </Button>
              ))}
          </li>
        ))}
      </ul>
    </section>
  );
}
