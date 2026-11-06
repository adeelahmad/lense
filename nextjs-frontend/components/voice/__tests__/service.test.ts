import { Voice } from "@/app/openapi-client";
import type { Client } from "@/app/openapi-client/client";

import { generateSpeech, transcribeMedia } from "../service";

jest.mock("@/app/openapi-client", () => ({ Voice: { speakText: jest.fn(), transcribeClip: jest.fn() } }));
jest.mock("@/lib/api/browser", () => ({
  data: async (call: Promise<{ data?: unknown; error?: unknown }>) => {
    const result = await call;
    if (result.error) throw result.error;
    return result.data;
  },
}));
const client = {} as Client;
const signal = new AbortController().signal;

describe("raw voice service", () => {
  it("sends exact text and selected voice with server-only semantics", async () => {
    const clip = new Blob(["audio"], { type: "audio/wav" });
    jest.mocked(Voice.speakText).mockResolvedValue({ data: clip } as never);
    const text = "  # Raw [1]\n*text* and https://example.com  ";
    await expect(generateSpeech(client, text, "alloy", signal)).resolves.toBe(clip);
    expect(Voice.speakText).toHaveBeenCalledWith({ client, body: { text, voice: "alloy", server_only: true }, parseAs: "blob", signal });
  });
  it("does not call the server with oversized text", async () => {
    await expect(generateSpeech(client, "a".repeat(4001), "", signal)).rejects.toThrow(/4,000/);
    expect(Voice.speakText).not.toHaveBeenCalled();
  });
  it("rejects empty and non-audio successes instead of falling back to browser speech", async () => {
    for (const value of [undefined, new Blob([]), new Blob(["error"], { type: "text/plain" })]) {
      jest.mocked(Voice.speakText).mockResolvedValue({ data: value } as never);
      await expect(generateSpeech(client, "Hi", "", signal)).rejects.toThrow(/no audio/);
    }
  });
  it("surfaces server failures without replacing the user's request", async () => {
    jest.mocked(Voice.speakText).mockResolvedValue({ error: new Error("Speech provider unavailable") } as never);
    await expect(generateSpeech(client, "Hi", "", signal)).rejects.toThrow("Speech provider unavailable");
  });
  it("sends the file as raw bytes and preserves its content type", async () => {
    const file = new File(["media"], "clip.webm", { type: "video/webm" });
    jest.mocked(Voice.transcribeClip).mockResolvedValue({ data: { text: "Exact transcript." } } as never);
    await expect(transcribeMedia(client, file, signal)).resolves.toBe("Exact transcript.");
    expect(Voice.transcribeClip).toHaveBeenCalledWith({ client, body: file, headers: { "Content-Type": "video/webm" }, signal });
  });
});
