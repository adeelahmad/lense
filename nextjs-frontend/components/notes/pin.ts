"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";

import { Notes } from "@/app/openapi-client";
import type { NoteTree } from "@/app/openapi-client/types.gen";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";

/** Pins a note for this person, or unpins it: pinned notes show first on their Notes page and at the top of their
 * tree. It shows at once, and goes back if the server says no. */
export function usePin() {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  return useCallback(
    async (ns: string, pid: number, on: boolean) => {
      const key = ["notes-tree", ns];
      const set = (pinned: boolean) =>
        qc.setQueryData<NoteTree>(key, (t) =>
          t ? { ...t, pages: t.pages.map((p) => (p.id === pid ? { ...p, pinned } : p)) } : t,
        );
      set(on);
      try {
        await data((on ? Notes.pinPage : Notes.unpinPage)({ client, path: { pid } }));
      } catch (err) {
        set(!on);
        toast({
          title: on ? "Couldn’t pin it" : "Couldn’t unpin it",
          body: err instanceof ApiError ? err.message : "Please try again.",
          tone: "red",
        });
      }
      qc.invalidateQueries({ queryKey: ["note", pid] });
    },
    [client, qc, toast],
  );
}
