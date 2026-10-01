"use client";

import { useQueryClient } from "@tanstack/react-query";
import { FileAudio, FileVideo, Paperclip } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { kindOf, localProblem } from "@/components/import/files";
import { chooseFiles } from "@/components/import/pending";
import { sentShare, sentText, uploadError } from "@/components/import/upload-model";
import { sendFile } from "@/components/import/uploader";
import { isMedia, useUploadLimits } from "@/components/import/use-import";
import { useRec } from "@/components/recording/context";
import { rk } from "@/components/recording/hooks";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { useToast } from "@/components/ui/toast";
import { ApiError, useApiClient } from "@/lib/api/browser";
import { bytes } from "@/lib/format";

type Phase = "idle" | "sending" | "paused" | "failed";

/**
 * Attach the audio (or video) a transcript-only recording came from (docs/api.md, Uploads). It goes up in pieces and
 * becomes the recording's media; the transcript and its speakers stay. Closing the dialog stops sending, and choosing
 * the same file again carries on where it stopped.
 */
export function AttachAudioDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const { id, ns } = useRec();
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const limits = useUploadLimits();
  const [file, setFile] = useState<File | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [sent, setSent] = useState(0);
  const stop = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!open) stop.current?.abort();
  }, [open]);

  const pick = async () => {
    const [f] = await chooseFiles(limits.extensions.join(","));
    if (!f) return;
    const p = isMedia(kindOf(f.name)) ? localProblem(f, limits) : { title: "Choose an audio or video file" };
    setFile(p ? null : f);
    setProblem(p?.title ?? null);
    setSent(0);
    setPhase("idle");
  };

  const send = async () => {
    if (!file) return;
    const ctrl = new AbortController();
    stop.current = ctrl;
    setPhase("sending");
    setProblem(null);
    try {
      await sendFile(client, file, {
        namespace: ns ?? "", // the server takes the recording's when it isn't known here
        attach: id,
        pieceMb: limits.chunk_mb,
        signal: ctrl.signal,
        onProgress: (u) => setSent(u.offset),
      });
      void qc.invalidateQueries({ queryKey: rk.all(id) });
      void qc.invalidateQueries({ queryKey: ["recordings"] });
      void qc.invalidateQueries({ queryKey: ["jobs"] });
      toast({
        title: "Audio attached",
        body: "Its waveform is being drawn; the transcript stays as it is.",
        tone: "green",
      });
      setFile(null);
      setPhase("idle");
      onOpenChange(false);
    } catch (e) {
      if (ctrl.signal.aborted) return setPhase("paused");
      setPhase("failed");
      setProblem(uploadError(e instanceof ApiError ? e.status : 0, e instanceof Error ? e.message : String(e)));
    }
  };

  const share = file ? sentShare(sent, file.size) : 0;
  const Icon = file && kindOf(file.name) === "video" ? FileVideo : FileAudio;
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Attach audio"
      description="Upload the audio or video this transcript came from. The transcript and its speakers stay as they are; the player gets the media and its waveform, and speakers are told apart by voice when the transcript didn’t name them."
    >
      <div className="flex flex-col gap-4">
        {file ? (
          <div className="flex flex-col gap-2.5 rounded-[10px] border border-border bg-surface px-3.5 py-3">
            <div className="flex items-center gap-2.5">
              <Icon className="size-[18px] shrink-0 text-fg-secondary" aria-hidden />
              <span className="min-w-0 flex-1 truncate text-[14px] font-semibold text-fg">{file.name}</span>
              <span className="tabular shrink-0 text-[12.5px] text-fg-muted">{bytes(file.size)}</span>
            </div>
            {phase !== "idle" && (
              <>
                <span
                  className="relative block h-[5px] overflow-hidden rounded-pill bg-surface-neutral"
                  role="progressbar"
                  aria-label={`Attaching ${file.name}`}
                  aria-valuenow={Math.round(share * 100)}
                >
                  <span
                    className={
                      phase === "failed"
                        ? "block h-full bg-red"
                        : phase === "paused"
                          ? "block h-full bg-fg-muted"
                          : "block h-full bg-blue"
                    }
                    style={{ width: `${Math.max(2, Math.round(share * 100))}%` }}
                  />
                </span>
                <span className="tabular text-[12.5px] text-fg-secondary">
                  {phase === "paused" ? "Paused · " : ""}
                  {sentText(sent, file.size)}
                </span>
              </>
            )}
          </div>
        ) : (
          <p className="text-[13px] leading-[1.5] text-fg-secondary">
            {limits.extensions.map((e) => e.slice(1)).join(", ")}, up to {bytes(limits.max_mb * 1024 * 1024)}. It goes
            up in pieces: if the connection drops, choosing the same file again carries on where it stopped.
          </p>
        )}
        {problem && <Banner tone="error">{problem}</Banner>}
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {phase === "sending" ? "Stop" : "Cancel"}
          </Button>
          {phase !== "sending" && (
            <Button variant="secondary" icon={<Paperclip />} onClick={() => void pick()}>
              {file ? "Another file…" : "Choose a file…"}
            </Button>
          )}
          {phase === "sending" ? (
            <Button variant="secondary" onClick={() => stop.current?.abort()}>
              Pause
            </Button>
          ) : (
            <Button
              variant="primary"
              disabled={!file}
              disabledReason="Choose an audio or video file first"
              onClick={() => void send()}
            >
              {phase === "paused" ? "Resume" : phase === "failed" ? "Try again" : "Attach"}
            </Button>
          )}
        </div>
      </div>
    </Dialog>
  );
}
