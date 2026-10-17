"use client";

import { useSession } from "next-auth/react";
import { useCallback } from "react";

import { downloadWithToken } from "@/components/library/actions";
import { useToast } from "@/components/ui/toast";
import { ApiError } from "@/lib/api/browser";

/** Downloads notes as Markdown, for other notes apps: one note as a .md file, or a namespace's notes as a zip of them
 * in folders that follow the tree. */
export function useNoteDownloads() {
  const { data: session } = useSession();
  const token = session?.accessToken;
  const toast = useToast();
  const get = useCallback(
    (url: string, name: string) =>
      downloadWithToken(url, token, name).catch((err) =>
        toast({
          title: "Couldn’t download it",
          body: err instanceof ApiError ? err.message : "Please try again.",
          tone: "red",
        }),
      ),
    [token, toast],
  );
  return {
    note: (pid: number, title: string) => get(`/api/v1/notes/${pid}/markdown`, `${title || "Untitled"}.md`),
    all: (ns: string) => get(`/api/v1/notes/export?ns=${encodeURIComponent(ns)}`, `${ns} notes.zip`),
  };
}
