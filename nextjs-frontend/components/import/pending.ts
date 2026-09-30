"use client";

import { useRouter } from "next/navigation";
import { useCallback } from "react";

/**
 * Files picked or dropped outside the import page (Home's quick import, the library's empty state, a drop anywhere on
 * the library) wait here while the app navigates to /import, which takes them on mount. In memory only: a reload
 * starts over, which is what people expect of a file picker.
 */
let pending: { files: File[]; namespace: string | null } | null = null;

export function stashFiles(files: File[], namespace: string | null = null) {
  pending = files.length ? { files, namespace } : null;
}

export function takeFiles(): {
  files: File[];
  namespace: string | null;
} | null {
  const p = pending;
  pending = null;
  return p;
}

/** Open the system file picker; resolves with the chosen files (empty when cancelled). */
export function chooseFiles(accept?: string): Promise<File[]> {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.multiple = true;
    if (accept) input.accept = accept;
    input.style.display = "none";
    input.addEventListener("change", () => {
      resolve(Array.from(input.files ?? []));
      input.remove();
    });
    input.addEventListener("cancel", () => {
      resolve([]);
      input.remove();
    });
    document.body.appendChild(input);
    input.click();
  });
}

/** Send files to the import page: `open(files)` for dropped files, `pick()` to choose them first. */
export function useSendToImport(namespace: string | null = null) {
  const router = useRouter();
  const open = useCallback(
    (files: File[]) => {
      if (!files.length) return;
      stashFiles(files, namespace);
      router.push("/import");
    },
    [router, namespace],
  );
  const pick = useCallback(async () => open(await chooseFiles()), [open]);
  return { open, pick };
}

/** Where an import goes by default: the top bar's namespace if you can import there, else your busiest one. */
export function defaultImportNamespace(
  namespaces: { name: string; recordings?: number | unknown }[],
  canEdit: (ns: string) => boolean,
  current: string | null,
): string | null {
  if (current && canEdit(current)) return current;
  const mine = namespaces.filter((n) => canEdit(n.name));
  mine.sort((a, b) => (Number(b.recordings) || 0) - (Number(a.recordings) || 0));
  return mine[0]?.name ?? null;
}

/** Whether a drag carries files (not text or a link being dragged around the page). */
export function isFileDrag(e: { dataTransfer: DataTransfer | null }): boolean {
  return Array.from(e.dataTransfer?.types ?? []).includes("Files");
}
