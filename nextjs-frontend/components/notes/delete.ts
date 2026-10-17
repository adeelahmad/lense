"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useCallback } from "react";

import { Notes } from "@/app/openapi-client";
import { pageOf } from "@/components/notes/links";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";

/** Deletes a note at once, with Undo on the notice instead of a question first: a deleted note waits in recently
 * deleted (on the Notes page) for 30 days, and Undo puts it back where it was and opens it. Resolves true once it's
 * deleted. */
export function useDeleteNote() {
  const client = useApiClient();
  const qc = useQueryClient();
  const router = useRouter();
  const toast = useToast();
  const fail = useCallback(
    (title: string) => (err: unknown) =>
      toast({ title, body: err instanceof ApiError ? err.message : "Please try again.", tone: "red" }),
    [toast],
  );
  const refresh = useCallback(() => {
    qc.invalidateQueries({ queryKey: ["notes-tree"] });
    qc.invalidateQueries({ queryKey: ["notes-search"] });
    qc.invalidateQueries({ queryKey: ["notes-deleted"] });
  }, [qc]);
  return useCallback(
    async (pid: number, title: string): Promise<boolean> => {
      try {
        await data(Notes.deletePage({ client, path: { pid } }));
      } catch (err) {
        fail("Couldn’t delete it")(err);
        return false;
      }
      qc.removeQueries({ queryKey: ["note", pid] });
      refresh();
      toast({
        title: `Deleted “${title}”`,
        body: "It’s in Recently deleted on the Notes page for 30 days.",
        duration: 8000,
        action: {
          label: "Undo",
          onClick: () =>
            void data(Notes.restoreDeleted({ client, path: { pid } }))
              .then((p) => {
                qc.setQueryData(["note", pid], p);
                refresh();
                router.push(p.about ? pageOf(p.about) : `/notes/${pid}`);
              })
              .catch(fail("Couldn’t put it back")),
        },
      });
      return true;
    },
    [client, qc, router, toast, fail, refresh],
  );
}
