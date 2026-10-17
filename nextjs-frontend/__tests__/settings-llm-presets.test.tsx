import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Admin, Setup } from "@/app/openapi-client";
import { SettingsSection } from "@/components/settings/section";
import type { SettingsView } from "@/components/settings/model";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Admin: { updateSettings: jest.fn(), testLlm: jest.fn() },
  Setup: { detectLlm: jest.fn(), llmProviders: jest.fn() },
  Metadata: {},
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/lib/hooks/session", () => ({ useArchive: () => ({ namespaces: [] }) }));
jest.mock("@/components/ui/toast", () => ({ useToast: () => jest.fn() }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const m = (f: unknown) => f as jest.Mock;

const view = (keySet: boolean, locked: string[] = []) =>
  ({
    llm: {
      values: {
        base_url: "http://localhost:11434/v1",
        model: "llama3.1",
        chat_models: [],
        vision_model: null,
        describe_max: 20,
        describe_engine: "llm",
        florence_model: "base",
        api_key: { secret: true, set: keySet },
        api_key_env: null,
        max_chars: 24000,
        timeout: 120,
      },
      locked,
    },
  }) as unknown as SettingsView;

function show(v: SettingsView) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <SettingsSection id="llm" view={v} onErrors={() => undefined} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

describe("Settings › LLM provider presets", () => {
  beforeEach(() => {
    m(Admin.updateSettings)
      .mockReset()
      .mockImplementation(() => ok({ ok: true }));
    m(Setup.detectLlm)
      .mockReset()
      .mockImplementation(() =>
        ok([{ kind: "Ollama", base_url: "http://localhost:11434/v1", models: ["llama3.1"], suggested: "llama3.1" }]),
      );
    m(Setup.llmProviders)
      .mockReset()
      .mockImplementation(() =>
        ok([
          {
            name: "OpenAI",
            base_url: "https://api.openai.com/v1",
            model: "gpt-5-mini",
            models: ["gpt-5-mini", "gpt-5"],
            key_url: "https://platform.openai.com/api-keys",
          },
        ]),
      );
  });

  it("switches to a cloud provider with one click and a pasted key", async () => {
    show(view(false));
    expect(await screen.findByRole("button", { name: /Ollama/ })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(await screen.findByRole("button", { name: "OpenAI" }));
    expect(screen.getByLabelText("Base URL (OpenAI-compatible)")).toHaveValue("https://api.openai.com/v1");
    expect(screen.getByLabelText("Model")).toHaveValue("gpt-5-mini");
    const key = screen.getByLabelText("API key");
    await waitFor(() => expect(key).toHaveFocus()); // ready for the paste
    expect(screen.getByRole("link", { name: "Get a key from OpenAI" })).toBeInTheDocument();
    fireEvent.change(key, { target: { value: "sk-test" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(Admin.updateSettings).toHaveBeenCalledTimes(1));
    expect(m(Admin.updateSettings).mock.calls[0][0]).toMatchObject({
      path: { section: "llm" },
      body: { base_url: "https://api.openai.com/v1", model: "gpt-5-mini", api_key: "sk-test" },
    });
  });

  it("opens the saved key for replacing when the provider changes", async () => {
    show(view(true));
    expect(screen.getByText(/^Set/)).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "OpenAI" }));
    await waitFor(() => expect(screen.getByPlaceholderText("Paste the new key")).toHaveFocus());
  });

  it("has Florence-2 describe pictures on this server in one pick", async () => {
    show(view(true));
    expect(screen.getByText("Describing pictures")).toBeInTheDocument();
    expect(screen.getByLabelText("Model that can see images")).toBeInTheDocument();
    expect(screen.queryByLabelText("Florence-2 size")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Describe pictures with"), { target: { value: "florence-2" } });
    expect(screen.getByLabelText("Florence-2 size")).toHaveValue("base"); // the small one, for a Raspberry Pi
    expect(screen.queryByLabelText("Model that can see images")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(Admin.updateSettings).toHaveBeenCalledTimes(1));
    expect(m(Admin.updateSettings).mock.calls[0][0]).toMatchObject({
      path: { section: "llm" },
      body: { describe_engine: "florence-2" },
    });
  });

  it("offers no presets when .env sets the address", async () => {
    show(view(false, ["base_url"]));
    expect(screen.getByLabelText("Base URL (OpenAI-compatible)")).toHaveAttribute("readonly");
    expect(screen.queryByRole("button", { name: "OpenAI" })).not.toBeInTheDocument();
    expect(Setup.llmProviders).not.toHaveBeenCalled();
  });
});
