import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Voice } from "@/app/openapi-client";
import { SAMPLE, VoicePicker } from "@/components/settings/voice-picker";

jest.mock("@/app/openapi-client", () => ({ Voice: { listVoices: jest.fn(), speakText: jest.fn() } }));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));

const ok = (data: unknown, status = 200) => Promise.resolve({ data, response: { ok: true, status } });

function show(provider: string, value: string | null, onChange = jest.fn(), savedProvider = provider) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <VoicePicker provider={provider} savedProvider={savedProvider} state={{ value, onChange }} />
    </QueryClientProvider>,
  );
  return onChange;
}

describe("voice picker", () => {
  beforeEach(() => {
    (Voice.listVoices as jest.Mock).mockReset();
    (Voice.speakText as jest.Mock).mockReset();
  });

  it("offers the provider's voices and keeps the id picked", async () => {
    (Voice.listVoices as jest.Mock).mockImplementation(() =>
      ok({
        provider: "elevenlabs",
        voices: [
          { id: "v42", name: "Ada", detail: "female, british" },
          { id: "v7", name: "Bo", detail: null },
        ],
      }),
    );
    const onChange = show("elevenlabs", "old-id");
    expect(await screen.findByRole("option", { name: "Ada · female, british" })).toBeInTheDocument();
    const select = screen.getByLabelText("Voice");
    expect((Voice.listVoices as jest.Mock).mock.calls[0][0]).toMatchObject({ query: { provider: "elevenlabs" } });
    expect(screen.getByRole("option", { name: "old-id (not in the list)" })).toBeInTheDocument();
    fireEvent.change(select, { target: { value: "v42" } });
    expect(onChange).toHaveBeenCalledWith("v42");
    fireEvent.change(select, { target: { value: "" } });
    expect(onChange).toHaveBeenLastCalledWith(null);
  });

  it("falls back to typing an id when the list can't be had, and plays a voice", async () => {
    (Voice.listVoices as jest.Mock).mockImplementation(() =>
      ok({ provider: "deepgram", voices: [], error: "Deepgram has no API key yet" }),
    );
    (Voice.speakText as jest.Mock).mockImplementation(() => ok(new Blob(["ID3"]), 204));
    show("deepgram", "aura-2-orion-en");
    expect(await screen.findByText(/Deepgram has no API key yet\. Type a voice id instead\./)).toBeInTheDocument();
    expect(screen.getByLabelText("Voice")).toHaveValue("aura-2-orion-en");
    fireEvent.click(screen.getByRole("button", { name: "Hear this voice" }));
    await waitFor(() => expect(Voice.speakText).toHaveBeenCalledTimes(1));
    expect((Voice.speakText as jest.Mock).mock.calls[0][0]).toMatchObject({
      body: { text: SAMPLE, voice: "aura-2-orion-en" },
    });
    expect(await screen.findByText(/It couldn’t read it/)).toBeInTheDocument();
  });

  it("asks to save before hearing a provider not saved yet", async () => {
    (Voice.listVoices as jest.Mock).mockImplementation(() =>
      ok({ provider: "gemini", voices: [{ id: "Kore", name: "Kore", detail: "Firm" }] }),
    );
    show("gemini", null, jest.fn(), "openai");
    expect(await screen.findByRole("option", { name: "Default (Kore)" })).toBeInTheDocument();
    expect(screen.getByText("Save to hear a voice from this provider.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Hear this voice" })).toBeDisabled();
  });
});
