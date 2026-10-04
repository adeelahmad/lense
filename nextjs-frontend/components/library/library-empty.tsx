"use client";

import { HardDriveDownload, Upload } from "lucide-react";
import Link from "next/link";

import { useSendToImport } from "@/components/import/pending";
import { Button } from "@/components/ui/button";
import { needRole, useArchive } from "@/lib/hooks/session";

/** Library L4: a namespace with nothing in it yet. Two ways in: watch a folder, or bring in files. */
export function LibraryEmpty({ namespace }: { namespace: string | null }) {
  const { admin, can } = useArchive();
  const canImport = can("editor", namespace);
  const send = useSendToImport(namespace);
  return (
    <div className="grid flex-1 place-items-center border-t border-border px-4 py-10 md:px-10">
      <div className="flex max-w-[760px] flex-col items-center gap-6 text-center">
        <div className="flex flex-col items-center gap-2">
          <h2 className="text-[22px] font-bold leading-tight tracking-[-.01em] text-fg">
            {namespace ? `Nothing in ${namespace} yet` : "No recordings yet"}
          </h2>
          <p className="max-w-[520px] text-[15px] leading-[1.55] text-fg-secondary">
            Watch a folder so new recordings arrive on their own, or bring in audio and transcripts you already have.
          </p>
        </div>
        <div className="grid w-full gap-4 text-left sm:grid-cols-2">
          <div className="flex flex-col gap-3 rounded-lg border border-border p-5">
            <div className="flex items-center gap-2.5">
              <span className="grid size-9 place-items-center rounded-[10px] bg-blue-surface text-blue">
                <HardDriveDownload className="size-[18px]" aria-hidden />
              </span>
              <span className="text-[15px] font-bold text-fg">Connect a source</span>
            </div>
            <p className="text-[13.5px] leading-normal text-fg-secondary">
              S3, Dropbox, Google Drive, OneDrive, SFTP, SMB, WebDAV or local disk. Pick folders, choose a pipeline, and
              optionally backfill what’s there.
            </p>
            {admin ? (
              <Button asChild variant="primary" size="sm" className="self-start">
                <Link href="/sources?add=1">Connect a source</Link>
              </Button>
            ) : (
              <Button
                variant="primary"
                size="sm"
                className="self-start"
                disabled
                disabledReason="Only admins can connect sources. Ask an admin to watch a folder for this namespace."
              >
                Connect a source
              </Button>
            )}
          </div>
          <div className="flex flex-col gap-3 rounded-lg border-[1.5px] border-dashed border-border p-5">
            <div className="flex items-center gap-2.5">
              <span className="grid size-9 place-items-center rounded-[10px] bg-surface-neutral text-fg-secondary">
                <Upload className="size-[18px]" aria-hidden />
              </span>
              <span className="text-[15px] font-bold text-fg">Drop files to import</span>
            </div>
            <p className="text-[13.5px] leading-normal text-fg-secondary">
              Transcripts as txt, md, docx, pdf, srt, vtt or json. You’ll see a preview before anything is saved.
            </p>
            <Button
              variant="secondary"
              size="sm"
              className="self-start"
              onClick={send.pick}
              disabled={!canImport}
              disabledReason={needRole("editor", namespace)}
            >
              Choose files
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
