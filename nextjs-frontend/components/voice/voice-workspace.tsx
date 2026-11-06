"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { Voice } from "@/app/openapi-client";
import { Button } from "@/components/ui/button";
import { Field, Input, Select, Textarea } from "@/components/ui/field";
import { Panel } from "@/components/ui/panel";
import { data, useApiClient } from "@/lib/api/browser";

import { audioFilename, mediaProblem, textProblem } from "./model";
import { generateSpeech, transcribeMedia } from "./service";

function useBlobUrl(blob: Blob | null) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!blob) {
      setUrl(null);
      return;
    }
    const next = URL.createObjectURL(blob);
    setUrl(next);
    return () => URL.revokeObjectURL(next);
  }, [blob]);
  return url;
}

export function VoiceWorkspace() {
  const client = useApiClient();
  const info = useQuery({
    queryKey: ["voice-workspace", "info"],
    queryFn: () => data(Voice.voiceInfo({ client })),
    retry: false,
  });
  const voices = useQuery({
    queryKey: ["voice-workspace", "voices"],
    queryFn: () => data(Voice.listVoices({ client })),
    retry: false,
  });
  const [text, setText] = useState("");
  const [voice, setVoice] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [audio, setAudio] = useState<Blob | null>(null);
  const [transcript, setTranscript] = useState<string | null>(null);
  const [transcriptBlob, setTranscriptBlob] = useState<Blob | null>(null);
  const [busy, setBusy] = useState<"speech" | "transcript" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const pending = useRef<AbortController | null>(null);
  const audioUrl = useBlobUrl(audio);
  const transcriptUrl = useBlobUrl(transcriptBlob);
  useEffect(() => () => pending.current?.abort(), []);

  function cancel() {
    pending.current?.abort();
    pending.current = null;
    setBusy(null);
  }

  async function run(kind: "speech" | "transcript") {
    if (pending.current) return;
    const problem = kind === "speech" ? textProblem(text) : mediaProblem(file);
    if (problem) {
      setError(problem);
      return;
    }
    const request = new AbortController();
    pending.current = request;
    setBusy(kind);
    setError(null);
    setCopied(false);
    if (kind === "speech") setAudio(null);
    else {
      setTranscript(null);
      setTranscriptBlob(null);
    }
    try {
      if (kind === "speech") {
        const clip = await generateSpeech(client, text, voice, request.signal);
        if (!request.signal.aborted) setAudio(clip);
      } else if (file) {
        const words = await transcribeMedia(client, file, request.signal);
        if (!request.signal.aborted) {
          setTranscript(words);
          setTranscriptBlob(new Blob([words], { type: "text/plain;charset=utf-8" }));
        }
      }
    } catch (e) {
      if (!request.signal.aborted) setError(e instanceof Error ? e.message : "The request failed. Try again.");
    } finally {
      if (pending.current === request) {
        pending.current = null;
        setBusy(null);
      }
    }
  }

  const speechDisabled = Boolean(busy || !info.data?.speak || textProblem(text));
  const transcriptDisabled = Boolean(busy || !info.data?.transcribe || mediaProblem(file));
  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-5 p-4 sm:p-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-fg">Voice</h1>
          <p className="mt-1 text-sm text-fg-secondary">Turn text into audio, or an audio or video file into text.</p>
        </div>
        <Button asChild>
          <Link href="/chat">Back to Chat</Link>
        </Button>
      </header>
      <Panel
        title="Make an episode from your sources"
        subtitle="Original narration, a summary, an explanation or a two-voice dialogue."
      >
        <p className="mb-3 text-sm text-fg-secondary">
          Import files or URLs through the existing import flow, then select sources in your Library and choose Create
          podcast. Originals use the selected source text; generated episodes cite their sources.
        </p>
        <div className="flex flex-wrap gap-2">
          <Button asChild>
            <Link href="/import">Import sources</Link>
          </Button>
          <Button asChild>
            <Link href="/library">Choose from Library</Link>
          </Button>
          <Button asChild>
            <Link href="/podcasts">Open Podcasts</Link>
          </Button>
        </div>
      </Panel>
      <Panel
        title="Your speech services"
        actions={
          <Button
            size="sm"
            disabled={Boolean(busy || info.isFetching || voices.isFetching)}
            onClick={() => {
              setVoice("");
              void info.refetch();
              void voices.refetch();
            }}
          >
            Refresh status
          </Button>
        }
      >
        <div className="space-y-2 text-sm text-fg-secondary" aria-live="polite">
          {info.isPending ? (
            <p>Checking server capabilities…</p>
          ) : info.error ? (
            <p role="alert">{info.error.message}</p>
          ) : (
            <>
              <p>
                Text to speech:{" "}
                {info.data?.speak
                  ? `configured (${voices.data?.provider || "server provider"})`
                  : "unavailable on this server"}
                .
              </p>
              <p>
                Speech to text:{" "}
                {info.data?.transcribe
                  ? `configured (${info.data.engine || "server engine"})`
                  : "unavailable on this server"}
                .
              </p>
            </>
          )}
          <p>
            Uses the local or remote providers selected in{" "}
            <Link className="text-fg-accent underline" href="/settings/ai">
              AI assistant settings
            </Link>
            . Remote providers receive the text or media you submit and may charge for use. Configured services can
            still be loading or unavailable.
          </p>
          <p>
            This workspace requires server audio. It never switches to browser speech. Chat keeps its own browser
            fallback.
          </p>
        </div>
      </Panel>
      {error && (
        <div role="alert" className="rounded-md border border-red-border bg-red-surface p-3 text-sm text-red-dark">
          {error}
        </div>
      )}
      {busy && (
        <div className="flex flex-wrap items-center gap-3" role="status">
          <span>{busy === "speech" ? "Generating audio…" : "Transcribing media…"}</span>
          <Button onClick={cancel}>Cancel</Button>
          <span className="text-xs text-fg-muted">
            Cancel stops waiting; the server may finish work already started.
          </span>
        </div>
      )}
      <div className="grid items-start gap-5 lg:grid-cols-2">
        <Panel title="Text to speech" subtitle="Your exact text, without chat, rewriting or summarizing.">
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              if (!speechDisabled) void run("speech");
            }}
          >
            <Field
              label="Text to read"
              hint={`${Array.from(text).length.toLocaleString()} / 4,000 characters`}
              error={text && textProblem(text)}
            >
              {({ id, describedBy, invalid }) => (
                <Textarea
                  id={id}
                  aria-describedby={describedBy}
                  invalid={invalid}
                  autoFocus
                  rows={9}
                  value={text}
                  disabled={Boolean(busy)}
                  onChange={(e) => setText(e.target.value)}
                  placeholder="Paste or type the text you want to hear."
                />
              )}
            </Field>
            <Field label="Voice" hint="Applies to this clip only; your saved default is unchanged.">
              {({ id, describedBy }) => (
                <Select
                  id={id}
                  aria-describedby={describedBy}
                  value={voice}
                  disabled={Boolean(busy || voices.isPending)}
                  onChange={(e) => setVoice(e.target.value)}
                  options={[
                    { value: "", label: `Saved default${voices.data?.current ? ` (${voices.data.current})` : ""}` },
                    ...(voices.data?.voices ?? []).map((v) => ({
                      value: v.id,
                      label: v.detail ? `${v.name} · ${v.detail}` : v.name,
                    })),
                  ]}
                />
              )}
            </Field>
            {(voices.error || voices.data?.error) && (
              <p role="alert" className="text-sm text-red-dark">
                Could not list voices: {voices.error?.message || voices.data?.error}. You can still use the saved
                default.
              </p>
            )}
            <Button
              type="submit"
              variant="primary"
              disabled={speechDisabled}
              disabledReason={
                busy
                  ? "Wait for the current request or cancel it."
                  : !info.data?.speak
                    ? "Configure a server speech provider in Settings, then refresh status."
                    : textProblem(text)
              }
            >
              Generate audio
            </Button>
          </form>
          {audio && audioUrl && (
            <div className="mt-4 space-y-3" aria-live="polite">
              <p className="text-sm text-fg-secondary">Generated audio · synthesized speech</p>
              <audio aria-label="Generated speech" controls src={audioUrl} className="w-full" />
              <Button asChild>
                <a href={audioUrl} download={audioFilename(audio.type)}>
                  Download audio
                </a>
              </Button>
            </div>
          )}
        </Panel>
        <Panel title="Media to text" subtitle="Transcribe a file without adding it to your Library.">
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              if (!transcriptDisabled) void run("transcript");
            }}
          >
            <Field
              label="Audio or video file"
              hint="Up to 25 MiB and 5 minutes. Audio formats such as WAV, MP3, WebM, Ogg and MP4; decoding depends on the server."
              error={file && mediaProblem(file)}
            >
              {({ id, describedBy, invalid }) => (
                <Input
                  id={id}
                  type="file"
                  accept="audio/*,video/*,.ogg,.webm,.wav,.mp3,.m4a,.mp4,.flac"
                  aria-describedby={describedBy}
                  invalid={invalid}
                  disabled={Boolean(busy)}
                  onChange={(e) => {
                    setFile(e.target.files?.[0] ?? null);
                    setTranscript(null);
                    setTranscriptBlob(null);
                    setCopied(false);
                  }}
                />
              )}
            </Field>
            <Button
              type="submit"
              variant="primary"
              disabled={transcriptDisabled}
              disabledReason={
                busy
                  ? "Wait for the current request or cancel it."
                  : !info.data?.transcribe
                    ? "Configure speech to text in Settings, then refresh status."
                    : mediaProblem(file)
              }
            >
              Transcribe file
            </Button>
          </form>
          {transcript !== null && (
            <div className="mt-4 space-y-3" aria-live="polite">
              <Field label="Transcript">{({ id }) => <Textarea id={id} rows={9} readOnly value={transcript} />}</Field>
              {!transcript && (
                <p className="text-sm text-fg-secondary">No speech was detected. Try a clearer recording.</p>
              )}
              <div className="flex flex-wrap gap-2">
                <Button
                  disabled={!transcript}
                  onClick={async () => {
                    try {
                      await navigator.clipboard.writeText(transcript);
                      setCopied(true);
                    } catch {
                      setError("Clipboard access is unavailable. Select the transcript to copy it, or download it.");
                    }
                  }}
                >
                  {copied ? "Copied" : "Copy transcript"}
                </Button>
                {transcriptUrl && (
                  <Button asChild>
                    <a href={transcriptUrl} download="lens-transcript.txt">
                      Download text
                    </a>
                  </Button>
                )}
              </div>
            </div>
          )}
        </Panel>
      </div>
    </div>
  );
}
