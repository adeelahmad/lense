"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { Podcasts, Voice } from "@/app/openapi-client";
import type { PickedLine } from "@/app/openapi-client/types.gen";
import { MODES, selectedVoices, type PodcastMode } from "@/components/podcasts/create-model";
import { LENGTHS, STYLES, type Style } from "@/components/podcasts/model";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select, Textarea } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";

/** Make an episode from the picked resources and lines, then open its page. */
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
  const voices = useQuery({
    queryKey: ["podcast-create", "voices"],
    queryFn: () => data(Voice.listVoices({ client })),
    enabled: open,
    retry: false,
  });
  const [mode, setMode] = useState<PodcastMode>("dialogue");
  const [voiceA, setVoiceA] = useState("");
  const [voiceB, setVoiceB] = useState("");
  const starting = useRef(false);
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
    if (starting.current || (!recordings.length && !excerpts.length)) return;
    starting.current = true;
    setBusy(true);
    setError(null);
    try {
      const body = {
        selection: { recordings, excerpts },
        mode,
        voices: selectedVoices(mode, voiceA, voiceB),
        prompt: mode === "original" ? null : prompt.trim() || null,
        length,
        style,
        title: title.trim() || null,
      };
      const out = await data(
        Podcasts.createPodcast({
          client,
          body,
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
      starting.current = false;
      setBusy(false);
    }
  };

  const picked = STYLES.find((s) => s.value === style);
  const pickedMode = MODES.find((m) => m.value === mode);
  const voiceOptions = [
    { value: "", label: "Podcast default" },
    ...(voices.data?.voices ?? []).map((v) => ({
      value: v.id,
      label: v.detail ? `${v.name} · ${v.detail}` : v.name,
    })),
  ];
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!starting.current) onOpenChange(next);
      }}
      title="Create podcast"
      description={`Make an audio episode from ${what}. ${pickedMode?.hint ?? ""}`}
      actions={
        <>
          <Button variant="ghost" disabled={busy} onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="primary" disabled={busy || (!recordings.length && !excerpts.length)} onClick={start}>
            {busy ? "Starting…" : "Create podcast"}
          </Button>
        </>
      }
    >
      <Field label="Output" hint={pickedMode?.hint}>
        {(f) => (
          <Select
            id={f.id}
            aria-describedby={f.describedBy}
            value={mode}
            disabled={busy}
            onChange={(e) => setMode(e.target.value as PodcastMode)}
            options={MODES.map((m) => ({ value: m.value, label: m.label }))}
          />
        )}
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label={mode === "dialogue" ? "First host's voice" : "Narrator's voice"} hint="Uses this provider's actual voice IDs. Blank keeps the podcast default.">
          {(f) => (
            <Select
              id={f.id}
              aria-describedby={f.describedBy}
              value={voiceA}
              disabled={busy || voices.isPending}
              onChange={(e) => setVoiceA(e.target.value)}
              options={voiceOptions}
            />
          )}
        </Field>
        {mode === "dialogue" && (
          <Field label="Second host's voice">
            {(f) => (
              <Select
                id={f.id}
                value={voiceB}
                disabled={busy || voices.isPending}
                onChange={(e) => setVoiceB(e.target.value)}
                options={voiceOptions}
              />
            )}
          </Field>
        )}
      </div>
      <p className="text-[13px] text-fg-secondary">
        {voices.isPending ? "Loading provider voices…" : `Speech provider: ${voices.data?.provider || "configured in Settings"}.`}
        {" "}These choices apply to this episode only. Remote providers receive the script and may charge for use.
      </p>
      {(voices.error || voices.data?.error) && (
        <p role="alert" className="text-[13px] text-red-dark">
          Could not list voices: {voices.error?.message || voices.data?.error}. You can keep the podcast defaults.
          <Button size="sm" variant="link" disabled={busy || voices.isFetching} onClick={() => { setVoiceA(""); setVoiceB(""); void voices.refetch(); }}>Retry voice list</Button>
        </p>
      )}
      {mode !== "original" && (
        <>
          <Field label="Angle" optional hint="What to focus on, or who it's for.">
            {(f) => (
              <Textarea
                id={f.id}
                aria-describedby={f.describedBy}
                rows={2}
                value={prompt}
                disabled={busy}
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
                  disabled={busy}
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
                  disabled={busy}
                  onChange={(e) => setStyle(e.target.value as Style)}
                  options={STYLES.map((s) => ({ value: s.value, label: s.label }))}
                />
              )}
            </Field>
          </div>
        </>
      )}
      <Field label="Title" optional hint="Left empty, the episode's own title is used.">
        {(f) => (
          <Input
            id={f.id}
            aria-describedby={f.describedBy}
            value={title}
            disabled={busy}
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

