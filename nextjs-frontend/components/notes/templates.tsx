"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";

import { Notes } from "@/app/openapi-client";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { FindBox } from "@/components/ui/find-box";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";

/** Makes a new note from a page (a template, or any note to copy) with its text, drawings, files and filing, and
 * opens it with its title selected, ready to type over. Resolves the new note's id, or null when it couldn't. */
export function useCopyNote() {
  const client = useApiClient();
  const qc = useQueryClient();
  const router = useRouter();
  const toast = useToast();
  return useCallback(
    async (pid: number, opts: { title?: string; parent?: number | null } = {}) => {
      try {
        const p = await data(
          Notes.copyPage({ client, path: { pid }, body: { title: opts.title ?? null, parent: opts.parent ?? null } }),
        );
        qc.invalidateQueries({ queryKey: ["notes-tree"] });
        router.push(`/notes/${p.id}?new=1`);
        return p.id;
      } catch (err) {
        toast({
          title: "Couldn’t make the note",
          body: err instanceof ApiError ? err.message : "Please try again.",
          tone: "red",
        });
        return null;
      }
    },
    [client, qc, router, toast],
  );
}

/** Picks one of a namespace's templates (the notes inside its Templates note) and starts a new note from it: type to
 * find, Enter takes the first. With none yet, it offers to make the Templates note. */
export function TemplatePicker({ ns, onClose }: { ns: string; onClose: () => void }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const router = useRouter();
  const toast = useToast();
  const copy = useCopyNote();
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const list = useQuery({
    queryKey: ["notes-templates", ns],
    queryFn: () => data(Notes.listTemplates({ client, query: { ns } })),
  });
  const words = q.trim().toLowerCase();
  const items = (list.data?.templates ?? [])
    .filter((t) => !words || t.title.toLowerCase().includes(words))
    .map((t) => ({ id: t.id, name: t.title, meta: t.summary ?? undefined }));
  const makeRoot = async () => {
    setBusy(true);
    try {
      const root = await data(Notes.templatesNote({ client, body: { ns } }));
      qc.invalidateQueries({ queryKey: ["notes-tree"] });
      onClose();
      router.push(`/notes/${root.id}`);
    } catch (err) {
      toast({
        title: "Couldn’t make the Templates note",
        body: err instanceof ApiError ? err.message : "Please try again.",
        tone: "red",
      });
    } finally {
      setBusy(false);
    }
  };
  const none = list.isSuccess && !list.data.templates.length;
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="New note from a template"
      description={
        none
          ? "A template is any note inside the Templates note: put notes there to start new ones from them."
          : undefined
      }
      actions={
        none && !list.data.root ? (
          <Button variant="primary" onClick={() => void makeRoot()} disabled={busy}>
            Make the Templates note
          </Button>
        ) : none && list.data.root ? (
          <Button
            variant="primary"
            onClick={() => {
              onClose();
              router.push(`/notes/${list.data.root}`);
            }}
          >
            Open the Templates note
          </Button>
        ) : undefined
      }
    >
      {!none && (
        <FindBox
          q={q}
          onQ={setQ}
          items={items}
          loading={list.isPending}
          empty={list.isPending ? undefined : `No template is called “${q.trim()}”.`}
          onPick={async (it) => {
            setBusy(true);
            if ((await copy(it.id)) != null) onClose();
            setBusy(false);
          }}
          onCancel={onClose}
          placeholder="The template to start from"
          label="Template"
          autoFocus
          busy={busy}
        />
      )}
    </Dialog>
  );
}
