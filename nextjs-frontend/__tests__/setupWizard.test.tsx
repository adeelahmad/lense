import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";
import type { ReactNode } from "react";

import { Admin, Setup, type SetupView } from "@/app/openapi-client";
import { SetupWizard, isLocalModel } from "@/components/setup/setup-wizard";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Setup: {
    getSetup: jest.fn(),
    saveNamespace: jest.fn(),
    saveLlm: jest.fn(),
    detectLlm: jest.fn(),
    llmProviders: jest.fn(),
    saveStorage: jest.fn(),
    saveTelemetry: jest.fn(),
    saveOauth: jest.fn(),
    saveBudget: jest.fn(),
    finish: jest.fn(),
  },
  Admin: { testLlm: jest.fn(), testTelemetry: jest.fn() },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
const replace = jest.fn();
jest.mock("next/navigation", () => ({ useRouter: () => ({ replace, refresh: jest.fn() }) }));
jest.mock("@/lib/hooks/session", () => ({ useArchive: () => ({ me: { user: { email: "ada@x.io" } } }) }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const m = (f: unknown) => f as jest.Mock;

const CLOUD = [
  {
    name: "OpenAI",
    base_url: "https://api.openai.com/v1",
    model: "gpt-5-mini",
    models: ["gpt-5-mini", "gpt-5"],
    key_url: "https://platform.openai.com/api-keys",
  },
  {
    name: "Anthropic",
    base_url: "https://api.anthropic.com/v1",
    model: "claude-opus-5-5",
    models: ["claude-opus-5-5", "claude-sonnet-5-5"],
    key_url: "https://console.anthropic.com/settings/keys",
  },
];

const VIEW = (over: Partial<SetupView> = {}): SetupView => ({
  pending: true,
  todo: ["llm"],
  admin: { from_env: false },
  namespace: { existing: [], locked: false },
  llm: { values: { base_url: null, model: null, api_key: { secret: true, set: false } }, locked: [] },
  storage: {
    data_dir: "/data",
    database: "surrealkv:///data/surrealdb",
    embedded: true,
    local_roots: ["/media"],
    max_upload_mb: 4096,
    watches: 0,
  },
  telemetry: { enabled: false, endpoint: null, locked: [] },
  oauth: { enabled: true, access_minutes: 60, refresh_days: 30 },
  budget: {
    hard_usd: null,
    soft_usd: null,
    period: "month",
    on_over: "ask",
    price_in: null,
    price_out: null,
    model: null,
  },
  ...over,
});

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>{ui}</TooltipProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  m(Setup.detectLlm).mockImplementation(() => ok([]));
  m(Setup.llmProviders).mockImplementation(() => ok(CLOUD));
});

describe("SetupWizard", () => {
  it("starts at the model provider when the namespace is set, filled in from a server found nearby", async () => {
    m(Setup.getSetup).mockImplementation(() => ok(VIEW({ namespace: { existing: ["media"], locked: false } })));
    m(Setup.detectLlm).mockImplementation(() =>
      ok([
        {
          kind: "Ollama",
          base_url: "http://host.docker.internal:11434/v1",
          models: ["qwen3:8b", "nomic-embed-text"],
          suggested: "qwen3:8b",
        },
        { kind: "LM Studio", base_url: "http://localhost:1234/v1", models: ["gemma-3"], suggested: "gemma-3" },
      ]),
    );
    m(Setup.saveLlm).mockImplementation(() => ok({ ok: true }));
    wrap(<SetupWizard />);
    await waitFor(() => expect(screen.getByLabelText("Base URL")).toHaveValue("http://host.docker.internal:11434/v1"));
    expect(screen.getByLabelText("Model")).toHaveValue("qwen3:8b");
    expect(screen.getByText(/Pick one of the 2 on Ollama/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /LM Studio/ }));
    expect(screen.getByLabelText("Model")).toHaveValue("gemma-3");
    fireEvent.click(screen.getByRole("button", { name: "Save and continue" }));
    await waitFor(() =>
      expect(m(Setup.saveLlm).mock.calls[0][0].body).toEqual({
        base_url: "http://localhost:1234/v1",
        model: "gemma-3",
        api_key: null,
      }),
    );
  });

  it("fills in a cloud provider's address and model in one click, then waits for its key", async () => {
    m(Setup.getSetup).mockImplementation(() => ok(VIEW({ namespace: { existing: ["media"], locked: false } })));
    m(Setup.saveLlm).mockImplementation(() => ok({ ok: true }));
    wrap(<SetupWizard />);
    fireEvent.click(await screen.findByRole("button", { name: "OpenAI" }));
    expect(screen.getByLabelText("Base URL")).toHaveValue("https://api.openai.com/v1");
    expect(screen.getByLabelText("Model")).toHaveValue("gpt-5-mini");
    expect(screen.getByRole("button", { name: "OpenAI" })).toHaveAttribute("aria-pressed", "true");
    const key = screen.getByLabelText(/API key/);
    await waitFor(() => expect(key).toHaveFocus()); // ready for the paste
    expect(screen.getByRole("link", { name: "Get a key from OpenAI" })).toHaveAttribute(
      "href",
      "https://platform.openai.com/api-keys",
    );
    fireEvent.change(key, { target: { value: "sk-test" } });
    fireEvent.click(screen.getByRole("button", { name: "Save and continue" }));
    await waitFor(() =>
      expect(m(Setup.saveLlm).mock.calls[0][0].body).toEqual({
        base_url: "https://api.openai.com/v1",
        model: "gpt-5-mini",
        api_key: "sk-test",
      }),
    );
  });

  it("asks for the model provider and finishes, with the rest a click away", async () => {
    m(Setup.getSetup).mockImplementation(() => ok(VIEW()));
    m(Setup.saveNamespace).mockImplementation(() => ok({ ok: true }));
    m(Setup.saveLlm).mockImplementation(() => ok({ ok: true }));
    m(Admin.testLlm).mockImplementation(() => ok({ ok: true, reply: "OK", ms: 120, model: "llama3" }));
    m(Setup.saveStorage).mockImplementation(() => ok({ ok: true }));
    m(Setup.finish).mockImplementation(() => ok({ ok: true }));
    wrap(<SetupWizard />);

    // the first namespace has a default: not asked, but there to choose
    fireEvent.click(await screen.findByRole("button", { name: /Namespace/ }));
    const name = await screen.findByLabelText("Name");
    fireEvent.change(name, { target: { value: "Bad Name" } });
    expect(screen.getByRole("button", { name: "Create and continue" })).toBeDisabled();
    fireEvent.change(name, { target: { value: "family" } });
    fireEvent.click(screen.getByRole("button", { name: "Create and continue" }));
    await waitFor(() =>
      expect(Setup.saveNamespace).toHaveBeenCalledWith(
        expect.objectContaining({ body: { name: "family", graph: "shared" } }),
      ),
    );

    // so is storage: back to the model provider afterwards
    fireEvent.click(await screen.findByRole("button", { name: /Storage/ }));
    fireEvent.change(await screen.findByLabelText(/Largest upload/), { target: { value: "2048" } });
    fireEvent.click(screen.getByRole("button", { name: "Save and continue" }));
    await waitFor(() => expect(Setup.saveStorage).toHaveBeenCalled());
    expect(m(Setup.saveStorage).mock.calls[0][0].body).toEqual({ max_upload_mb: 2048, folder: null, namespace: null });

    fireEvent.change(await screen.findByLabelText("Base URL"), { target: { value: "http://localhost:11434/v1" } });
    fireEvent.change(screen.getByLabelText("Model"), { target: { value: "llama3" } });
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    expect(await screen.findByText(/llama3 answered “OK”/)).toBeInTheDocument();
    expect(Setup.finish).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Save and continue" }));
    await waitFor(() => expect(m(Setup.saveLlm).mock.calls.length).toBe(2));
    expect(m(Setup.saveLlm).mock.calls[1][0].body).toEqual({
      base_url: "http://localhost:11434/v1",
      model: "llama3",
      api_key: null,
    });

    // a model on this machine costs nothing: no budget step, and nothing else to ask
    await waitFor(() =>
      expect(Setup.finish).toHaveBeenCalledWith(expect.objectContaining({ body: { skipped: false } })),
    );
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/"));
    expect(screen.queryByRole("button", { name: /Budget/ })).not.toBeInTheDocument();
  });

  it("finishes on its own when there is nothing left to ask", async () => {
    m(Setup.getSetup).mockImplementation(() =>
      ok(
        VIEW({
          todo: [],
          llm: {
            values: { base_url: "http://x/v1", model: "qwen3:8b", api_key: { secret: true, set: false } },
            locked: [],
          },
        }),
      ),
    );
    m(Setup.finish).mockImplementation(() => ok({ ok: true }));
    wrap(<SetupWizard />);
    await waitFor(() =>
      expect(Setup.finish).toHaveBeenCalledWith(expect.objectContaining({ body: { skipped: false } })),
    );
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/"));
    expect(screen.queryByLabelText("Base URL")).not.toBeInTheDocument();
  });

  it("asks only for the budget when a paid model is set already", async () => {
    m(Setup.getSetup).mockImplementation(() =>
      ok(
        VIEW({
          todo: ["budget"],
          llm: {
            values: { base_url: "https://api.openai.com/v1", model: "gpt-x", api_key: { secret: true, set: true } },
            locked: ["base_url", "model", "api_key"],
          },
        }),
      ),
    );
    m(Setup.saveBudget).mockImplementation(() => ok(["budget"]));
    m(Setup.finish).mockImplementation(() => ok({ ok: true }));
    wrap(<SetupWizard />);
    fireEvent.change(await screen.findByLabelText("Hard limit"), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "Save and continue" }));
    await waitFor(() => expect(Setup.saveBudget).toHaveBeenCalled());
    await waitFor(() => expect(Setup.finish).toHaveBeenCalled());
  });

  it("asks for a budget after a cloud model: one scale for the hard limit, with the model's price", async () => {
    m(Setup.getSetup).mockImplementation(() =>
      ok(
        VIEW({
          namespace: { existing: ["media"], locked: false },
          llm: {
            values: { base_url: "https://api.openai.com/v1", model: "gpt-x", api_key: { secret: true, set: true } },
            locked: [],
          },
        }),
      ),
    );
    m(Setup.saveLlm).mockImplementation(() => ok({ ok: true }));
    m(Setup.saveBudget).mockImplementation(() => ok(["budget"]));
    m(Setup.finish).mockImplementation(() => ok({ ok: true }));
    wrap(<SetupWizard />);
    fireEvent.click(await screen.findByRole("button", { name: "Save and continue" }));

    const limit = await screen.findByLabelText("Hard limit");
    expect(screen.getByText("No limit")).toBeInTheDocument();
    fireEvent.change(limit, { target: { value: "3" } });
    expect(screen.getByText("$25 a month")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Input price"), { target: { value: "3" } });
    expect(screen.getByRole("button", { name: "Save and continue" })).toBeDisabled(); // both prices or none
    fireEvent.change(screen.getByLabelText("Output price"), { target: { value: "15" } });
    fireEvent.click(screen.getByRole("button", { name: "Save and continue" }));
    await waitFor(() => expect(Setup.saveBudget).toHaveBeenCalled());
    expect(m(Setup.saveBudget).mock.calls[0][0].body).toEqual({
      hard_usd: 25,
      soft_usd: null,
      period: "month",
      on_over: "ask",
      price_in: 3,
      price_out: 15,
    });

    // and that's all there is to ask
    await waitFor(() => expect(Setup.finish).toHaveBeenCalled());
    expect(screen.queryByLabelText(/Largest upload/)).not.toBeInTheDocument();
  });

  it("opts in to telemetry with an endpoint, after a test span", async () => {
    m(Setup.getSetup).mockImplementation(() => ok(VIEW({ namespace: { existing: ["media"], locked: false } })));
    m(Setup.saveTelemetry).mockImplementation(() => ok({ ok: true }));
    m(Admin.testTelemetry).mockImplementation(() => ok({ ok: true, ms: 12 }));
    m(Setup.finish).mockImplementation(() => ok({ ok: true }));
    wrap(<SetupWizard />);
    fireEvent.click(await screen.findByRole("button", { name: /Telemetry/ }));
    fireEvent.click(await screen.findByRole("radio", { name: "On" }));
    const save = screen.getByRole("button", { name: "Save and continue" });
    expect(save).toBeDisabled(); // on needs somewhere to send to
    fireEvent.change(screen.getByLabelText("OTLP endpoint"), { target: { value: "localhost:4318" } });
    expect(save).toBeDisabled();
    fireEvent.change(screen.getByLabelText("OTLP endpoint"), { target: { value: "http://localhost:4318" } });
    fireEvent.click(screen.getByRole("button", { name: "Send a test span" }));
    expect(await screen.findByText(/took a test span in 12 ms/)).toBeInTheDocument();
    expect(m(Setup.saveTelemetry).mock.calls[0][0].body).toEqual({ enabled: false, endpoint: "http://localhost:4318" });
    fireEvent.click(save);
    await waitFor(() => expect(m(Setup.saveTelemetry).mock.calls.length).toBe(2));
    expect(m(Setup.saveTelemetry).mock.calls[1][0].body).toEqual({ enabled: true, endpoint: "http://localhost:4318" });
    // back to what's still to ask: the model provider
    expect(await screen.findByLabelText("Base URL")).toBeInTheDocument();
    expect(Setup.finish).not.toHaveBeenCalled();
  });

  it("lets apps sign in for a set time, or turns it off", async () => {
    m(Setup.getSetup).mockImplementation(() => ok(VIEW({ namespace: { existing: ["media"], locked: false } })));
    m(Setup.saveOauth).mockImplementation(() => ok({ ok: true }));
    wrap(<SetupWizard />);
    fireEvent.click(await screen.findByRole("button", { name: /Apps and AI/ }));
    const minutes = await screen.findByLabelText(/Access token lasts/);
    fireEvent.change(minutes, { target: { value: "2" } });
    expect(screen.getByRole("button", { name: "Save and continue" })).toBeDisabled();
    fireEvent.change(minutes, { target: { value: "30" } });
    fireEvent.change(screen.getByLabelText(/stay signed in/), { target: { value: "14" } });
    fireEvent.click(screen.getByRole("button", { name: "Save and continue" }));
    await waitFor(() => expect(Setup.saveOauth).toHaveBeenCalled());
    expect(m(Setup.saveOauth).mock.calls[0][0].body).toEqual({ enabled: true, access_minutes: 30, refresh_days: 14 });
    // back to the model provider; to apps again and off: nothing else to choose
    fireEvent.click(await screen.findByRole("button", { name: /Apps and AI/ }));
    fireEvent.click(await screen.findByRole("radio", { name: "Off" }));
    expect(screen.queryByLabelText("MCP server address")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save and continue" }));
    await waitFor(() => expect(m(Setup.saveOauth).mock.calls.length).toBe(2));
    expect(m(Setup.saveOauth).mock.calls[1][0].body).toEqual({ enabled: false });
  }, 20_000); // two saves and two visits to the step: slow on a busy CI runner

  it("shows what .env sets as locked", async () => {
    m(Setup.getSetup).mockImplementation(() =>
      ok(
        VIEW({
          namespace: { existing: ["media"], locked: true },
          llm: {
            values: { base_url: "http://vllm:8000/v1", model: null, api_key: { secret: true, set: true } },
            locked: ["base_url", "api_key"],
          },
        }),
      ),
    );
    wrap(<SetupWizard />);
    const base = await screen.findByLabelText("Base URL"); // nothing to choose for the namespace: straight here
    expect(Setup.detectLlm).not.toHaveBeenCalled(); // the address is set in .env: nothing to look for
    expect(screen.queryByRole("button", { name: "OpenAI" })).not.toBeInTheDocument();
    expect(base).toBeDisabled();
    expect(base).toHaveValue("http://vllm:8000/v1");
    expect(base).toHaveAccessibleDescription(/LENS_LLM_BASE_URL/);
    expect(screen.getByLabelText(/API key/)).toBeDisabled();
    expect(screen.getByLabelText("Model")).toBeEnabled();
  });

  it("counts a namespace seeded before the wizard as set up", async () => {
    m(Setup.getSetup).mockImplementation(() => ok(VIEW({ namespace: { existing: ["media"], locked: false } })));
    wrap(<SetupWizard />);
    expect(await screen.findByLabelText("Base URL")).toBeInTheDocument(); // the namespace step is skipped
    fireEvent.click(screen.getByRole("button", { name: /Namespace/ }));
    expect(await screen.findByText(/already has one/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create and continue" })).not.toBeInTheDocument();
    expect(Setup.saveNamespace).not.toHaveBeenCalled();
  });

  it("hands the rest to the assistant once a model is set", async () => {
    m(Setup.getSetup).mockImplementation(() =>
      ok(
        VIEW({
          namespace: { existing: ["media"], locked: false },
          llm: {
            values: { base_url: "http://x/v1", model: "qwen3:8b", api_key: { secret: true, set: false } },
            locked: [],
          },
        }),
      ),
    );
    m(Setup.finish).mockImplementation(() => ok({ ok: true }));
    wrap(<SetupWizard />);
    fireEvent.click(await screen.findByRole("button", { name: /Storage/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Finish with the assistant" }));
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/chat?setup=1"));
    expect(Setup.finish).toHaveBeenCalledWith(expect.objectContaining({ body: { skipped: false } }));
  });

  it("finishes with the defaults once a model is set", async () => {
    m(Setup.getSetup).mockImplementation(() =>
      ok(
        VIEW({
          llm: {
            values: { base_url: "http://x/v1", model: "qwen3:8b", api_key: { secret: true, set: false } },
            locked: [],
          },
        }),
      ),
    );
    m(Setup.finish).mockImplementation(() => ok({ ok: true }));
    wrap(<SetupWizard />);
    fireEvent.click(await screen.findByRole("button", { name: /Storage/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Finish with defaults" }));
    await waitFor(() =>
      expect(Setup.finish).toHaveBeenCalledWith(expect.objectContaining({ body: { skipped: false } })),
    );
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/"));
  });

  it("can skip the whole wizard", async () => {
    m(Setup.getSetup).mockImplementation(() => ok(VIEW()));
    m(Setup.finish).mockImplementation(() => ok({ ok: true }));
    wrap(<SetupWizard />);
    fireEvent.click(await screen.findByRole("button", { name: "Skip setup" }));
    await waitFor(() =>
      expect(Setup.finish).toHaveBeenCalledWith(expect.objectContaining({ body: { skipped: true } })),
    );
  });

  it("tells a model on this machine or its network from a cloud provider", () => {
    for (const u of [
      "http://localhost:11434/v1",
      "http://host.docker.internal:1234/v1",
      "http://ollama:11434/v1",
      "http://192.168.1.5:8080/v1",
      "http://[::1]:11434",
    ])
      expect(isLocalModel(u)).toBe(true);
    for (const u of ["https://api.openai.com/v1", "https://openrouter.ai/api/v1", "", null])
      expect(isLocalModel(u)).toBe(false);
  });
});
