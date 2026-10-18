"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Archive, FolderKanban, Plus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Projects } from "@/app/openapi-client";
import { useProjects } from "@/components/projects/data";
import { ProjectFields, useNewDraft, type ProjectDraft } from "@/components/projects/project-form";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { DateTime, EmptyState, PageHeader, SkeletonRows } from "@/components/ui/states";
import { data, useApiClient } from "@/lib/api/browser";
import { cn } from "@/lib/utils";

/** Start a project: its name, goal and namespaces (the instructions wait for its page), then open it. */
export function NewProjectDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const router = useRouter();
  const fresh = useNewDraft();
  const [draft, setDraft] = useState<ProjectDraft>(fresh);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setDraft(fresh);
      setError(null);
    }
    // a fresh draft each time it opens, not each time the namespaces load
  }, [open]);
  useEffect(() => {
    // the namespaces loaded after it opened: start from the current one
    if (open && !draft.namespaces.length && fresh.namespaces.length)
      setDraft((d) => ({ ...d, namespaces: fresh.namespaces }));
  }, [open, draft.namespaces.length, fresh.namespaces]);

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      const p = await data(
        Projects.createProject({
          client,
          body: { name: draft.name.trim(), goal: draft.goal.trim(), namespaces: draft.namespaces },
        }),
      );
      void qc.invalidateQueries({ queryKey: ["projects"] });
      void qc.invalidateQueries({ queryKey: ["chats"] });
      onOpenChange(false);
      router.push(`/projects/${p.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn’t start the project.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="New project"
      description="A goal you work toward with the assistant, in threads that all know what it’s for."
      actions={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="primary" disabled={busy || !draft.name.trim() || !draft.namespaces.length} onClick={create}>
            {busy ? "Starting…" : "Start project"}
          </Button>
        </>
      }
    >
      <ProjectFields draft={draft} onChange={setDraft} />
      {error && (
        <p role="alert" className="m-0 text-[13px] text-red-dark">
          {error}
        </p>
      )}
    </Dialog>
  );
}

/** /projects: your projects, the most recently changed first, and the archived ones a click away. */
export function ProjectsScreen() {
  const [archived, setArchived] = useState(false);
  const [creating, setCreating] = useState(false);
  const q = useProjects(archived);
  return (
    <div className="flex flex-col gap-4 px-4 py-5 md:px-6">
      <PageHeader
        className="mb-0"
        title={archived ? "Archived projects" : "Projects"}
        meta="Goals you work toward with the assistant, in threads."
        actions={
          <>
            <Button
              variant="ghost"
              size="sm"
              icon={archived ? <FolderKanban /> : <Archive />}
              onClick={() => setArchived((a) => !a)}
            >
              {archived ? "Current projects" : "Archived"}
            </Button>
            <Button variant="primary" size="sm" icon={<Plus />} onClick={() => setCreating(true)}>
              New project
            </Button>
          </>
        }
      />
      {q.isPending ? (
        <SkeletonRows rows={4} />
      ) : q.isError ? (
        <EmptyState tone="error" title="Couldn’t load projects">
          {(q.error as Error).message}
        </EmptyState>
      ) : !q.data.length ? (
        <EmptyState
          icon={<FolderKanban />}
          title={archived ? "No archived projects" : "No projects yet"}
          actions={
            archived ? undefined : (
              <Button variant="primary" icon={<Plus />} onClick={() => setCreating(true)}>
                New project
              </Button>
            )
          }
        >
          {archived
            ? "Projects you archive wait here, unchanged."
            : "Give it a goal and the namespaces it draws on. Its coordinator keeps track, and each thread works on one part."}
        </EmptyState>
      ) : (
        <ul className="divide-y divide-border rounded-md border border-border">
          {q.data.map((p) => (
            <li key={p.id}>
              <Link
                href={`/projects/${p.id}`}
                className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3 hover:bg-surface"
              >
                <FolderKanban aria-hidden className="size-4 shrink-0 text-fg-secondary" />
                <span className="flex min-w-0 flex-1 flex-col">
                  <span className="truncate font-semibold text-fg">{p.name}</span>
                  {p.goal && <span className="truncate text-[13px] text-fg-secondary">{p.goal}</span>}
                </span>
                <span className="text-[13px] text-fg-secondary">{(p.namespaces ?? []).join(", ")}</span>
                <span className={cn("text-[13px]", p.open ? "text-fg-secondary" : "text-fg-muted")}>
                  {p.threads ? `${p.open} open of ${p.threads}` : "No threads"}
                </span>
                <DateTime iso={p.updated_at ?? p.created_at} className="w-24 text-right text-[13px] text-fg-muted" />
              </Link>
            </li>
          ))}
        </ul>
      )}
      <NewProjectDialog open={creating} onOpenChange={setCreating} />
    </div>
  );
}
