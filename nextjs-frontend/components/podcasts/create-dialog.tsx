"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Podcasts } from "@/app/openapi-client";
import type { PickedLine } from "@/app/openapi-client/types.gen";
import { LENGTHS, STYLES, type Style } from "@/components/podcasts/model";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select, Textarea } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";

/** Make a two-host episode about the picked resources (and lines in them), then open its page. */
export function CreatePodcastDialog({
  open,
  onOpenChange,
  recordings = [],
  excerpts = [],
  what,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  recordings?: number[];
  excerpts?: PickedLine[];
  /** What's picked, for the description ("3 recordings", "“Weekly sync”"). */
  what: string;
  onCreated?: () => void;
}) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const router = useRouter();
  const [prompt, setPrompt] = useState("");
  const [length, setLength] = useState<number>(10);
  const [style, setStyle] = useState<Style>("deep-dive");
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) setError(null);
  }, [open]);

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      const out = await data(
        Podcasts.createPodcast({
          client,
          body: {
            selection: { recordings, excerpts },
            prompt: prompt.trim() || null,
            length,
            style,
            title: title.trim() || null,
          },
        }),
      );
      void qc.invalidateQueries({ queryKey: ["podcasts"] });
      onOpenChange(false);
      onCreated?.();
      toast({
        title: "Making your podcast",
        body:
          out.placed === "sources" ? `It goes in ${out.namespace}, with its sources.` : `It goes in ${out.namespace}.`,
        tone: "green",
      });
      setPrompt("");
      setTitle("");
      router.push(`/podcasts/${out.episode}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn’t start the podcast.");
    } finally {
      setBusy(false);
    }
  };

  const picked = STYLES.find((s) => s.value === style);
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Create podcast"
      description={`Two hosts talk through ${what}. Every claim cites its source and is fact-checked against it.`}
      actions={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="primary" disabled={busy || (!recordings.length && !excerpts.length)} onClick={start}>
            {busy ? "Starting…" : "Create podcast"}
          </Button>
        </>
      }
    >
      <Field label="Angle" optional hint="What to focus on, or who it's for.">
        {(f) => (
          <Textarea
            id={f.id}
            aria-describedby={f.describedBy}
            rows={2}
            value={prompt}
            maxLength={2000}
            placeholder="e.g. what changed in our pricing and why"
            onChange={(e) => setPrompt(e.target.value)}
          />
        )}
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Length">
          {(f) => (
            <Select
              id={f.id}
              value={String(length)}
              onChange={(e) => setLength(Number(e.target.value))}
              options={LENGTHS.map((n) => ({ value: String(n), label: `About ${n} minutes` }))}
            />
          )}
        </Field>
        <Field label="Style" hint={picked?.hint}>
          {(f) => (
            <Select
              id={f.id}
              aria-describedby={f.describedBy}
              value={style}
              onChange={(e) => setStyle(e.target.value as Style)}
              options={STYLES.map((s) => ({ value: s.value, label: s.label }))}
            />
          )}
        </Field>
      </div>
      <Field label="Title" optional hint="Left empty, the script's own title is used.">
        {(f) => (
          <Input
            id={f.id}
            aria-describedby={f.describedBy}
            value={title}
            maxLength={200}
            onChange={(e) => setTitle(e.target.value)}
          />
        )}
      </Field>
      {error && (
        <p role="alert" className="text-[13px] text-red-dark">
          {error}
        </p>
      )}
    </Dialog>
  );
}
