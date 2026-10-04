import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Admin } from "@/app/openapi-client";
import { SettingsSection } from "@/components/settings/section";
import type { SettingsView } from "@/components/settings/model";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Admin: { updateSettings: jest.fn(), testSpeech: jest.fn() },
  Metadata: {},
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/lib/hooks/session", () => ({ useArchive: () => ({ namespaces: [] }) }));
jest.mock("@/components/ui/toast", () => ({ useToast: () => jest.fn() }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const VIEW = {
  speech: {
    values: {
      openai_base_url: "https://api.openai.com/v1",
      openai_model: "whisper-1",
      openai_api_key: { secret: true, set: false },
      elevenlabs_base_url: "https://api.elevenlabs.io",
      elevenlabs_model: "scribe_v1",
      elevenlabs_api_key: { secret: true, set: false },
      assemblyai_base_url: "https://api.assemblyai.com",
      assemblyai_model: "universal",
      assemblyai_api_key: { secret: true, set: false },
      deepgram_base_url: "https://api.deepgram.com",
      deepgram_model: "nova-3",
      deepgram_api_key: { secret: true, set: true },
      sentiment: true,
      timeout: 1800,
    },
  },
} as unknown as SettingsView;

function show() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <SettingsSection id="speech-providers" view={VIEW} onErrors={() => undefined} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

describe("speech providers", () => {
  beforeEach(() => {
    (Admin.updateSettings as jest.Mock).mockReset().mockImplementation(() => ok({ ok: true }));
    (Admin.testSpeech as jest.Mock).mockReset().mockImplementation(() => ok({ ok: true, detail: "signed in", ms: 80 }));
  });

  it("shows each provider with its own address, and tests one", async () => {
    show();
    for (const name of ["OpenAI-compatible", "ElevenLabs", "AssemblyAI", "Deepgram"])
      expect(screen.getByRole("heading", { name })).toBeInTheDocument();
    expect(screen.getAllByLabelText("Address")).toHaveLength(4);
    fireEvent.click(screen.getAllByRole("button", { name: "Test" })[3]);
    await waitFor(() => expect(Admin.testSpeech).toHaveBeenCalledTimes(1));
    expect((Admin.testSpeech as jest.Mock).mock.calls[0][0]).toMatchObject({ query: { provider: "deepgram" } });
    expect(await screen.findByText(/signed in · 80 ms/)).toBeInTheDocument();
  });

  it("saves a custom address for a proxy", async () => {
    show();
    fireEvent.change(screen.getAllByLabelText("Address")[2], { target: { value: "https://api.eu.assemblyai.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(Admin.updateSettings).toHaveBeenCalledTimes(1));
    expect((Admin.updateSettings as jest.Mock).mock.calls[0][0]).toMatchObject({
      path: { section: "speech" },
      body: { assemblyai_base_url: "https://api.eu.assemblyai.com" },
    });
  });
});
