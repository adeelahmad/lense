import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Admin } from "@/app/openapi-client";
import { SettingsSection } from "@/components/settings/section";
import type { SettingsView } from "@/components/settings/model";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Admin: { updateSettings: jest.fn(), decisionStatus: jest.fn(), testDecisions: jest.fn() },
  Metadata: {},
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/lib/hooks/session", () => ({ useArchive: () => ({ namespaces: [] }) }));
jest.mock("@/components/ui/toast", () => ({ useToast: () => jest.fn() }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const web = (values: Record<string, unknown>) =>
  ({
    // The server's defaults for the rest of the section.
    ai: {
      values: {
        tools: true,
        browser: true,
        refine_notes: true,
        organise_notes: true,
        compact: true,
        untrusted_marking: true,
        untrusted_asks: true,
        injection_check: "patterns",
        disabled_tools: [],
        max_steps: 6,
        browser_steps: 25,
        max_transcript_reads: 20,
        confirm_over_recordings: 100,
        confirm_over_cost: null,
        price_in: null,
        price_out: null,
        compact_model: null,
        context_tokens: null,
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
    voice: {
      values: {
        input: "auto",
        stt: "same",
        tts_provider: "openai",
        tts_base_url: null,
        tts_model: null,
        tts_voice: null,
        tts_api_key: { secret: true, set: false },
        styletts2_url: "http://styletts2:8880",
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
        ...values,
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

describe("web search settings", () => {
  beforeEach(() => {
    (Admin.updateSettings as jest.Mock).mockReset().mockImplementation(() => ok({ ok: true }));
    (Admin.decisionStatus as jest.Mock).mockReset().mockImplementation(() => ok({}));
  });

  it("is off, and one switch turns it on with DuckDuckGo", async () => {
    show(web({}));
    expect(screen.queryByLabelText("Results per search")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("switch", { name: /Let the assistant search the web/ }));
    expect(screen.getByText(/an unofficial client/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(Admin.updateSettings).toHaveBeenCalledTimes(1));
    expect((Admin.updateSettings as jest.Mock).mock.calls[0][0]).toMatchObject({
      path: { section: "web" },
      body: { search: "duckduckgo" },
    });
  });

  it("asks for the address SearXNG answers on", async () => {
    show(web({ search: "searxng" }));
    const address = screen.getByLabelText("SearXNG address");
    fireEvent.change(address, { target: { value: "http://searxng:8080" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(Admin.updateSettings).toHaveBeenCalledTimes(1));
    expect((Admin.updateSettings as jest.Mock).mock.calls[0][0]).toMatchObject({
      path: { section: "web" },
      body: { searxng_url: "http://searxng:8080" },
    });
  });

  it("asks for a key for a keyed provider", () => {
    show(web({ search: "brave" }));
    expect(screen.getByText("Brave Search API key")).toBeInTheDocument();
    expect(screen.queryByLabelText("SearXNG address")).not.toBeInTheDocument();
  });
});
