"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { Play } from "lucide-react";
import { useId, useRef } from "react";
import { Voice } from "@/app/openapi-client";
import type { FieldState } from "@/components/settings/fields";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/field";
import { data, useApiClient } from "@/lib/api/browser";

export const SAMPLE = "Hello. This is the voice Lens answers in.";

const DEFAULT_LABEL: Record<string, string> = {
  openai: "alloy",
  elevenlabs: "a premade voice",
  deepgram: "aura-2-thalia-en",
  gemini: "Kore",
  say: "the Mac’s own voice",
  kokoro: "af_heart",
  styletts2: "its own reader",
};

/** voice.tts_voice: a voice from the list the provider gives (its id is kept), with a button to hear it. */
export function VoicePicker({
  provider,
  savedProvider,
  state,
}: {
  provider: string;
  savedProvider: string;
  state: FieldState;
}) {
  const client = useApiClient();
  const id = useId();
  const msg = `${id}-msg`;
  const audio = useRef<HTMLAudioElement | null>(null);
  const value = String(state.value ?? "");
  const list = useQuery({
    queryKey: ["voices", provider],
    queryFn: () => data(Voice.listVoices({ client, query: { provider } })),
    staleTime: 5 * 60_000,
  });
  const hear = useMutation({
    mutationFn: async () => {
      const r = await Voice.speakText({ client, body: { text: SAMPLE, voice: value || null }, parseAs: "blob" });
      if (r.response?.status !== 200 || !(r.data instanceof Blob) || !r.data.size)
        throw new Error("It couldn’t read it: check the provider’s key and address, then try again.");
      audio.current?.pause();
      const url = URL.createObjectURL(r.data);
      const a = new Audio(url);
      audio.current = a;
      a.onended = () => URL.revokeObjectURL(url);
      await a.play();
    },
  });

  const voices = list.data?.voices ?? [];
  const known = voices.some((v) => v.id === value);
  const options = [
    { value: "", label: `Default (${DEFAULT_LABEL[provider] ?? "the provider’s"})` },
    ...(value && !known ? [{ value, label: `${value} (not in the list)` }] : []),
    ...voices.map((v) => ({ value: v.id, label: v.detail ? `${v.name} · ${v.detail}` : v.name })),
  ];
  const unsaved = provider !== savedProvider;
  const note = state.error
    ? state.error
    : hear.error
      ? hear.error.message
      : list.data?.error
        ? voices.length
          ? list.data.error
          : `${list.data.error}. Type a voice id instead.`
        : unsaved
          ? "Save to hear a voice from this provider."
          : null;

  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <label htmlFor={id} className="text-[13px] font-bold leading-tight text-fg-strong">
        Voice
      </label>
      <div className="flex items-center gap-2">
        {voices.length ? (
          <Select
            id={id}
            aria-describedby={note ? msg : undefined}
            value={value}
            disabled={Boolean(state.locked)}
            onChange={(e) => state.onChange(e.target.value || null)}
            options={options}
          />
        ) : (
          <Input
            id={id}
            aria-describedby={note ? msg : undefined}
            className="font-mono"
            value={value}
            disabled={Boolean(state.locked)}
            placeholder={list.isPending ? "Loading voices…" : (DEFAULT_LABEL[provider] ?? "")}
            onChange={(e) => state.onChange(e.target.value || null)}
          />
        )}
        <Button
          size="sm"
          icon={<Play />}
          aria-label="Hear this voice"
          onClick={() => hear.mutate()}
          disabled={unsaved || hear.isPending}
        >
          {hear.isPending ? "Reading…" : "Hear"}
        </Button>
      </div>
      {note && (
        <span id={msg} className="text-[12.5px] leading-[1.45] text-fg-secondary">
          {note}
        </span>
      )}
    </div>
  );
}
