import { Voice } from "@/app/openapi-client";
import type { Client } from "@/app/openapi-client/client";
import { data } from "@/lib/api/browser";

import { mediaProblem, textProblem } from "./model";

/** Unlike chat read-aloud, raw speech preserves punctuation and never uses browser speech as a fallback. */
export async function generateSpeech(client: Client, text: string, voice: string, signal: AbortSignal): Promise<Blob> {
  const problem = textProblem(text);
  if (problem) throw new Error(problem);
  const body = { text, voice: voice || undefined, server_only: true };
  const clip = await data(
    Voice.speakText({
      client,
      body,
      parseAs: "blob",
      signal,
    }),
  );
  if (!(clip instanceof Blob) || !clip.size || !clip.type.startsWith("audio/")) {
    throw new Error(
      "The server returned no audio. Check the speech provider in Settings and try again. Browser speech was not used.",
    );
  }
  return clip;
}

export async function transcribeMedia(client: Client, file: File, signal: AbortSignal): Promise<string> {
  const problem = mediaProblem(file);
  if (problem) throw new Error(problem);
  const result = await data(
    Voice.transcribeClip({
      client,
      body: file,
      headers: { "Content-Type": file.type || "application/octet-stream" },
      signal,
    }),
  );
  return result.text;
}
