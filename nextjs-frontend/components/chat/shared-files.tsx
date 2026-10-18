"use client";

import { Download, Loader2 } from "lucide-react";
import { useSession } from "next-auth/react";
import { useState } from "react";

import type { SandboxFile } from "@/app/openapi-client/types.gen";
import { downloadWithToken } from "@/components/library/actions";
import { bytes } from "@/lib/format";

/** Where a file the code sandbox handed the person in a conversation downloads from. */
export function sharedFileUrl(chatId: number, fileId: string): string {
  return `/api/v1/chats/${chatId}/files/${encodeURIComponent(fileId)}`;
}

/** The files the code sandbox handed the person with this answer (share_sandbox_file), each a download. */
export function SharedFiles({ chatId, files }: { chatId: number | null; files: SandboxFile[] }) {
  const token = useSession().data?.accessToken;
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (chatId == null || !files.length) return null;
  const get = async (f: SandboxFile) => {
    setBusy(f.id);
    setError(null);
    try {
      await downloadWithToken(sharedFileUrl(chatId, f.id), token, f.name);
    } catch (e) {
      setError(e instanceof Error ? e.message : "The download failed");
    } finally {
      setBusy(null);
    }
  };
  return (
    <div className="flex flex-col gap-1">
      <ul className="m-0 flex list-none flex-wrap gap-1.5 p-0" aria-label="Files to download">
        {files.map((f) => (
          <li key={f.id}>
            <button
              type="button"
              onClick={() => void get(f)}
              disabled={busy === f.id}
              className="flex items-center gap-1.5 rounded-md border border-border bg-background px-2 py-1 text-[12.5px] hover:bg-surface-neutral disabled:opacity-60"
            >
              {busy === f.id ? (
                <Loader2 className="size-3.5 animate-spin text-fg-secondary" aria-hidden />
              ) : (
                <Download className="size-3.5 text-fg-secondary" aria-hidden />
              )}
              <span className="max-w-[220px] truncate font-semibold text-fg">{f.name}</span>
              <span className="text-fg-muted">{bytes(f.size)}</span>
            </button>
          </li>
        ))}
      </ul>
      {error && (
        <p role="alert" className="m-0 text-[12.5px] text-red-dark">
          {error}
        </p>
      )}
    </div>
  );
}
