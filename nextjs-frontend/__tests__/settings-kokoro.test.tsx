import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Admin, Voice } from "@/app/openapi-client";
import { SettingsSection } from "@/components/settings/section";
import type { SettingsView } from "@/components/settings/model";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Admin: { updateSettings: jest.fn(), decisionStatus: jest.fn(), testDecisions: jest.fn() },
  Voice: { listVoices: jest.fn(), speakText: jest.fn() },
  Metadata: {},
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/lib/hooks/session", () => ({ useArchive: () => ({ namespaces: [] }) }));
jest.mock("@/components/ui/toast", () => ({ useToast: () => jest.fn() }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const view = (voice: Record<string, unknown>) =>
  ({
    // The server's defaults for the rest of the section.
    ai: {
      values: {
        tools: true,
        browser: true,
        refine_notes: true,
        organise_notes: true,
        compact: true,
        auto_title: true,
        untrusted_marking: true,
        untrusted_asks: true,
        injection_check: "patterns",
        disabled_tools: [],
        max_steps: 6,
        max_tool_calls: 50,
        browser_steps: 25,
        max_transcript_reads: 20,
        confirm_over_recordings: 100,
        confirm_over_cost: null,
        price_in: null,
        price_out: null,
        compact_model: null,
        context_tokens: null,
        compact_at: 50,
        answer_comments: true,
      },
    },
    decisions: {
      values: {
        engine: "auto",
        base_url: "https://api.typesafe.ai/v1",
        model: "jev-latest",
        api_key: { secret: true, set: false },
        act_above: 0.8,
        timeout: 10,
        price_per_call: null,
        laya_model: "aac6fef/laya-mlx",
        laya_url: null,
      },
    },
    research: { values: { max_tool_calls: 40, max_minutes: 20, model: null, agents_skip_review: false } },
    voice: {
      values: {
        input: "auto",
        stt: "same",
        tts_provider: "openai",
        tts_base_url: null,
        tts_model: null,
        tts_voice: null,
        tts_api_key: { secret: true, set: false },
        kokoro_model: "fp32",
        styletts2_url: "http://styletts2:8880",
        ...voice,
      },
    },
    web: {
      values: {
        search: "off",
        searxng_url: null,
        api_key: { secret: true, set: false },
        results: 5,
        read_chars: 20000,
        render: false,
      },
    },
  }) as unknown as SettingsView;

function show(view: SettingsView) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <SettingsSection id="ai" view={view} onErrors={() => undefined} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

describe("Kokoro in the voice settings", () => {
  beforeEach(() => {
    (Admin.updateSettings as jest.Mock).mockReset().mockImplementation(() => ok({ ok: true }));
    (Admin.decisionStatus as jest.Mock).mockReset().mockImplementation(() => ok({}));
    (Voice.listVoices as jest.Mock).mockReset().mockImplementation(() => ok({ provider: "kokoro", voices: [] }));
  });

  it("reads on this machine, with the model size to pick", async () => {
    show(view({}));
    expect(screen.queryByLabelText("Kokoro model")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Spoken answers by"), { target: { value: "kokoro" } });
    expect(screen.getByText(/nothing is sent anywhere and nothing is billed/)).toBeInTheDocument();
    const size = screen.getByLabelText("Kokoro model");
    expect(size).toHaveValue("fp32");
    fireEvent.change(size, { target: { value: "int8" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(Admin.updateSettings).toHaveBeenCalledTimes(1));
    expect((Admin.updateSettings as jest.Mock).mock.calls[0][0]).toMatchObject({
      path: { section: "voice" },
      body: { tts_provider: "kokoro", kokoro_model: "int8" },
    });
  });
});
