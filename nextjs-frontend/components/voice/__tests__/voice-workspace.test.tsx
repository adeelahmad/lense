import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { Voice } from "@/app/openapi-client";

import { generateSpeech, transcribeMedia } from "../service";
import { VoiceWorkspace } from "../voice-workspace";

jest.mock("@/app/openapi-client", () => ({ Voice: { voiceInfo: jest.fn(), listVoices: jest.fn() } }));
jest.mock("@/lib/api/browser", () => ({ useApiClient: () => client, data: async (call: Promise<unknown>) => call }));
jest.mock("../service", () => ({ generateSpeech: jest.fn(), transcribeMedia: jest.fn() }));
const client = {};

function renderWorkspace() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}><VoiceWorkspace /></QueryClientProvider>);
}

beforeEach(() => {
  jest.mocked(Voice.voiceInfo).mockResolvedValue({ speak: true, transcribe: true, engine: "local" } as never);
  jest.mocked(Voice.listVoices).mockResolvedValue({ provider: "openai", current: "alloy", voices: [{ id: "nova", name: "Nova" }] } as never);
  URL.createObjectURL = jest.fn(() => "blob:test-voice");
  URL.revokeObjectURL = jest.fn();
});

it("generates exact text in the selected voice and exposes playback and download", async () => {
  jest.mocked(generateSpeech).mockResolvedValue(new Blob(["audio"], { type: "audio/wav" }));
  const view = renderWorkspace();
  await screen.findByRole("option", { name: "Nova" });
  const text = "  # Exact *words* [1]\nwith a newline.  ";
  fireEvent.change(screen.getByLabelText("Text to read"), { target: { value: text } });
  fireEvent.change(screen.getByLabelText("Voice"), { target: { value: "nova" } });
  fireEvent.click(screen.getByRole("button", { name: "Generate audio" }));
  const download = await screen.findByRole("link", { name: "Download audio" });
  expect(download).toHaveAttribute("download", "lens-speech.wav");
  expect(screen.getByLabelText("Generated speech")).toHaveAttribute("src", "blob:test-voice");
  expect(generateSpeech).toHaveBeenCalledWith(client, text, "nova", expect.any(AbortSignal));
  view.unmount();
  expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:test-voice");
});

it("cancels pending work and ignores its late result", async () => {
  let resolve!: (value: Blob) => void;
  jest.mocked(generateSpeech).mockImplementation(() => new Promise((done) => { resolve = done; }));
  renderWorkspace();
  await screen.findByRole("option", { name: "Nova" });
  fireEvent.change(screen.getByLabelText("Text to read"), { target: { value: "Hello" } });
  fireEvent.click(screen.getByRole("button", { name: "Generate audio" }));
  fireEvent.click(screen.getByRole("button", { name: "Generate audio" }));
  expect(generateSpeech).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(jest.mocked(generateSpeech).mock.calls[0][3].aborted).toBe(true);
  resolve(new Blob(["audio"], { type: "audio/mpeg" }));
  await waitFor(() => expect(screen.queryByRole("link", { name: "Download audio" })).not.toBeInTheDocument());
});

it("transcribes uploaded media and clears the old transcript when selecting another file", async () => {
  jest.mocked(transcribeMedia).mockResolvedValue("Words from the recording.");
  renderWorkspace();
  await screen.findByRole("option", { name: "Nova" });
  const file = new File(["audio"], "clip.wav", { type: "audio/wav" });
  fireEvent.change(screen.getByLabelText("Audio or video file"), { target: { files: [file] } });
  fireEvent.click(screen.getByRole("button", { name: "Transcribe file" }));
  expect(await screen.findByLabelText("Transcript")).toHaveValue("Words from the recording.");
  expect(await screen.findByRole("link", { name: "Download text" })).toHaveAttribute("download", "lens-transcript.txt");
  expect(transcribeMedia).toHaveBeenCalledWith(client, file, expect.any(AbortSignal));
  fireEvent.change(screen.getByLabelText("Audio or video file"), { target: { files: [new File(["next"], "next.wav")] } });
  expect(screen.queryByLabelText("Transcript")).not.toBeInTheDocument();
});

it("shows unavailable providers without silently using browser speech", async () => {
  jest.mocked(Voice.voiceInfo).mockResolvedValue({ speak: false, transcribe: false } as never);
  renderWorkspace();
  await screen.findByText("Text to speech: unavailable on this server.");
  fireEvent.change(screen.getByLabelText("Text to read"), { target: { value: "Hello" } });
  fireEvent.click(screen.getByRole("button", { name: "Generate audio" }));
  expect(generateSpeech).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Generate audio" })).toHaveAttribute("aria-disabled", "true");
});
