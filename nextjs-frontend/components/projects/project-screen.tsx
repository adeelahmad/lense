"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Archive, ArchiveRestore, Ellipsis, FolderKanban, MessagesSquare, Pencil, Plus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { Projects } from "@/app/openapi-client";
import type { Project, ProjectThread } from "@/app/openapi-client/types.gen";
import { useProject } from "@/components/projects/data";
import { ProjectFields, type ProjectDraft } from "@/components/projects/project-form";
import { Schedules } from "@/components/projects/schedules";
import { Badge } from "@/components/ui/badge";
import { Button, IconButton } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Checkbox, Textarea } from "@/components/ui/field";
import { Menu, MenuContent, MenuItem, MenuTrigger } from "@/components/ui/menu";
import { DateTime, EmptyState, PageHeader, SkeletonRows } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { cn } from "@/lib/utils";

function EditProjectDialog({
  project,
  open,
  onOpenChange,
}: {
  project: Project;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const client = useApiClient();
  const qc = useQueryClient();
  const draftOf = (p: Project): ProjectDraft => ({
    name: p.name,
    goal: p.goal ?? "",
    instructions: p.instructions ?? "",
    namespaces: p.namespaces ?? [],
  });
  const [draft, setDraft] = useState<ProjectDraft>(() => draftOf(project));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (open) {
      setDraft(draftOf(project));
      setError(null);
    }
    // the project as it is when the dialog opens
  }, [open]);

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const p = await data(
        Projects.updateProject({
          client,
          path: { pid: project.id },
          body: {
            name: draft.name.trim(),
            goal: draft.goal.trim(),
            instructions: draft.instructions.trim(),
            namespaces: draft.namespaces,
          },
        }),
      );
      qc.setQueryData(["project", project.id], p);
      void qc.invalidateQueries({ queryKey: ["projects"] });
      void qc.invalidateQueries({ queryKey: ["chats"] });
      onOpenChange(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn’t save the project.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Edit project"
      description="Its conversations follow what you save here from their next answer on."
      actions={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="primary" disabled={busy || !draft.name.trim() || !draft.namespaces.length} onClick={save}>
            {busy ? "Saving…" : "Save"}
          </Button>
        </>
      }
    >
      <ProjectFields draft={draft} onChange={setDraft} instructions />
      {error && (
        <p role="alert" className="m-0 text-[13px] text-red-dark">
          {error}
        </p>
      )}
    </Dialog>
  );
}

/** One field: what the new thread should do. Enter starts it (Shift+Enter for a new line). Left empty, an empty
 * thread opens to ask in; with a brief, it's answered in the background and the list shows it working. */
function NewThread({ project }: { project: Project }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const router = useRouter();
  const toast = useToast();
  const [brief, setBrief] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const start = async (e?: FormEvent) => {
    e?.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    const text = brief.trim();
    try {
      const out = await data(
        Projects.startThread({ client, path: { pid: project.id }, body: text ? { brief: text } : {} }),
      );
      void qc.invalidateQueries({ queryKey: ["project", project.id] });
      void qc.invalidateQueries({ queryKey: ["projects"] });
      void qc.invalidateQueries({ queryKey: ["chats"] });
      if (!text) {
        router.push(`/chat/${out.id}`);
        return;
      }
      setBrief("");
      toast({
        title: "Thread started",
        body: "It’s being answered in the background.",
        tone: "green",
        action: { label: "Open", onClick: () => router.push(`/chat/${out.id}`) },
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn’t start the thread.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={start} className="flex flex-col gap-2">
      <label htmlFor="project-brief" className="text-[13px] font-bold leading-tight text-fg-strong">
        New thread
      </label>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start">
        <Textarea
          id="project-brief"
          rows={2}
          className="min-h-[64px]"
          value={brief}
          maxLength={4000}
          placeholder="What should it work on? Leave empty to start a conversation."
          onChange={(e) => setBrief(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) void start(e);
          }}
        />
        <Button type="submit" variant="primary" icon={<Plus />} disabled={busy} className="shrink-0">
          {busy ? "Starting…" : "Start thread"}
        </Button>
      </div>
      {error && (
        <p role="alert" className="m-0 text-[13px] text-red-dark">
          {error}
        </p>
      )}
    </form>
  );
}

function ThreadRow({ project, thread }: { project: Project; thread: ProjectThread }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const mark = async (done: boolean) => {
    // shows at once; put back if the server says no
    qc.setQueryData<Project>(["project", project.id], (p) =>
      p ? { ...p, thread_list: p.thread_list?.map((t) => (t.id === thread.id ? { ...t, done } : t)) } : p,
    );
    try {
      await data(Projects.updateThread({ client, path: { pid: project.id, cid: thread.id }, body: { done } }));
    } catch (e) {
      toast({ title: "Couldn’t change the thread", body: e instanceof Error ? e.message : undefined, tone: "red" });
    }
    void qc.invalidateQueries({ queryKey: ["project", project.id] });
    void qc.invalidateQueries({ queryKey: ["projects"] });
  };
  return (
    <li className="flex items-start gap-3 px-4 py-3">
      <Checkbox
        className="mt-0.5"
        aria-label={thread.done ? `Mark “${thread.title}” not done` : `Mark “${thread.title}” done`}
        checked={thread.done ?? false}
        onCheckedChange={(v) => void mark(v)}
      />
      <Link href={`/chat/${thread.id}`} className="group flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="flex min-w-0 items-center gap-2">
          <span
            className={cn(
              "truncate font-semibold group-hover:underline",
              thread.done ? "text-fg-muted line-through" : "text-fg",
            )}
          >
            {thread.title}
          </span>
          {thread.answering && (
            <Badge tone="intent" dot>
              Working
            </Badge>
          )}
        </span>
        {thread.last && (
          <span className="line-clamp-2 text-[13px] text-fg-secondary">
            {thread.last.role === "user" ? "Asked: " : ""}
            {thread.last.text}
          </span>
        )}
      </Link>
      <DateTime iso={thread.updated_at ?? thread.created_at} className="shrink-0 text-[13px] text-fg-muted" />
    </li>
  );
}

/** /projects/{id}: the goal, its coordinator, a field to start a thread, and its threads, open ones first. */
export function ProjectScreen({ id }: { id: number }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const q = useProject(id);
  const [editing, setEditing] = useState(false);

  const archive = async (archived: boolean) => {
    try {
      const p = await data(Projects.updateProject({ client, path: { pid: id }, body: { archived } }));
      qc.setQueryData(["project", id], p);
      void qc.invalidateQueries({ queryKey: ["projects"] });
      if (archived)
        toast({
          title: "Project archived",
          tone: "intent",
          action: { label: "Undo", onClick: () => void archive(false) },
        });
    } catch (e) {
      toast({ title: "Couldn’t change the project", body: e instanceof Error ? e.message : undefined, tone: "red" });
    }
  };

  if (q.isPending) return <SkeletonRows rows={6} className="px-4 py-5 md:px-6" />;
  if (q.isError)
    return (
      <EmptyState
        tone="error"
        title={(q.error as Error).message === "not found" ? "This project isn’t here" : "Couldn’t load the project"}
        actions={
          <Button asChild variant="secondary">
            <Link href="/projects">All projects</Link>
          </Button>
        }
      >
        {(q.error as Error).message}
      </EmptyState>
    );

  const p = q.data;
  const threads = [...(p.thread_list ?? [])].sort((a, b) => Number(a.done ?? false) - Number(b.done ?? false));
  return (
    <div className="mx-auto flex w-full max-w-[920px] flex-col gap-5 px-4 py-5 md:px-6">
      <PageHeader
        className="mb-0"
        title={p.name}
        meta={
          <>
            {(p.namespaces ?? []).join(", ")}
            {p.archived && (
              <Badge className="ml-2" tone="gate">
                Archived
              </Badge>
            )}
          </>
        }
        actions={
          <>
            {p.coordinator != null && (
              <Button asChild variant="secondary" size="sm">
                <Link href={`/chat/${p.coordinator}`}>
                  <MessagesSquare aria-hidden className="size-4" />
                  Coordinator
                </Link>
              </Button>
            )}
            <Menu>
              <MenuTrigger asChild>
                <IconButton label="Project actions">
                  <Ellipsis />
                </IconButton>
              </MenuTrigger>
              <MenuContent align="end">
                <MenuItem icon={<Pencil />} onSelect={() => setEditing(true)}>
                  Edit
                </MenuItem>
                <MenuItem
                  icon={p.archived ? <ArchiveRestore /> : <Archive />}
                  onSelect={() => void archive(!p.archived)}
                >
                  {p.archived ? "Unarchive" : "Archive"}
                </MenuItem>
              </MenuContent>
            </Menu>
          </>
        }
      >
        <Link href="/projects" className="text-[13px] text-fg-secondary hover:underline">
          <FolderKanban aria-hidden className="mr-1 inline size-3.5 align-[-2px]" />
          Projects
        </Link>
      </PageHeader>

      {p.goal ? (
        <p className="m-0 whitespace-pre-wrap text-[15px] leading-relaxed text-fg">{p.goal}</p>
      ) : (
        <button
          type="button"
          onClick={() => setEditing(true)}
          className="self-start text-[14px] text-blue hover:underline"
        >
          Add its goal
        </button>
      )}
      {p.instructions && (
        <details className="text-[14px] text-fg-secondary">
          <summary className="cursor-pointer select-none font-semibold">Instructions</summary>
          <p className="mb-0 mt-2 whitespace-pre-wrap">{p.instructions}</p>
        </details>
      )}

      <NewThread project={p} />

      {threads.length ? (
        <ul aria-label="Threads" className="m-0 list-none divide-y divide-border rounded-md border border-border p-0">
          {threads.map((t) => (
            <ThreadRow key={t.id} project={p} thread={t} />
          ))}
        </ul>
      ) : (
        <p className="m-0 text-[14px] text-fg-secondary">
          No threads yet. Start one above, or ask the coordinator to plan the work and start them.
        </p>
      )}
      <Schedules project={p} />
      <EditProjectDialog project={p} open={editing} onOpenChange={setEditing} />
    </div>
  );
}
