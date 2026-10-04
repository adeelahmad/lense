import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";
import type { ReactNode } from "react";

import { Admin, Setup, type SetupView } from "@/app/openapi-client";
import { SetupWizard } from "@/components/setup/setup-wizard";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Setup: {
    getSetup: jest.fn(),
    saveNamespace: jest.fn(),
    saveLlm: jest.fn(),
    detectLlm: jest.fn(),
    saveStorage: jest.fn(),
    saveTelemetry: jest.fn(),
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

const VIEW = (over: Partial<SetupView> = {}): SetupView => ({
  pending: true,
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

  it("walks through namespace, model provider, storage and telemetry, then finishes", async () => {
    m(Setup.getSetup).mockImplementation(() => ok(VIEW()));
    m(Setup.saveNamespace).mockImplementation(() => ok({ ok: true }));
    m(Setup.saveLlm).mockImplementation(() => ok({ ok: true }));
    m(Admin.testLlm).mockImplementation(() => ok({ ok: true, reply: "OK", ms: 120, model: "llama3" }));
    m(Setup.saveStorage).mockImplementation(() => ok({ ok: true }));
    m(Setup.saveTelemetry).mockImplementation(() => ok({ ok: true }));
    m(Setup.finish).mockImplementation(() => ok({ ok: true }));
    wrap(<SetupWizard />);

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

    fireEvent.change(await screen.findByLabelText("Base URL"), { target: { value: "http://localhost:11434/v1" } });
    fireEvent.change(screen.getByLabelText("Model"), { target: { value: "llama3" } });
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    expect(await screen.findByText(/llama3 answered “OK”/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save and continue" }));
    await waitFor(() => expect(m(Setup.saveLlm).mock.calls.length).toBe(2));
    expect(m(Setup.saveLlm).mock.calls[1][0].body).toEqual({
      base_url: "http://localhost:11434/v1",
      model: "llama3",
      api_key: null,
    });

    fireEvent.change(await screen.findByLabelText(/Largest upload/), { target: { value: "2048" } });
    fireEvent.click(screen.getByRole("button", { name: "Save and continue" }));
    await waitFor(() => expect(Setup.saveStorage).toHaveBeenCalled());
    expect(m(Setup.saveStorage).mock.calls[0][0].body).toEqual({ max_upload_mb: 2048, folder: null, namespace: null });

    // telemetry is off unless chosen: finishing with it off sends nothing anywhere
    expect(await screen.findByText(/off unless you turn it on/)).toBeInTheDocument();
    expect(screen.queryByLabelText("OTLP endpoint")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save and finish" }));
    await waitFor(() =>
      expect(Setup.finish).toHaveBeenCalledWith(expect.objectContaining({ body: { skipped: false } })),
    );
    expect(m(Setup.saveTelemetry).mock.calls[0][0].body).toEqual({ enabled: false, endpoint: null });
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/"));
  });

  it("opts in to telemetry with an endpoint, after a test span", async () => {
    m(Setup.getSetup).mockImplementation(() => ok(VIEW({ namespace: { existing: ["media"], locked: false } })));
    m(Setup.saveTelemetry).mockImplementation(() => ok({ ok: true }));
    m(Admin.testTelemetry).mockImplementation(() => ok({ ok: true, ms: 12 }));
    m(Setup.finish).mockImplementation(() => ok({ ok: true }));
    wrap(<SetupWizard />);
    fireEvent.click(await screen.findByRole("button", { name: /Telemetry/ }));
    fireEvent.click(await screen.findByRole("radio", { name: "On" }));
    const save = screen.getByRole("button", { name: "Save and finish" });
    expect(save).toBeDisabled(); // on needs somewhere to send to
    fireEvent.change(screen.getByLabelText("OTLP endpoint"), { target: { value: "localhost:4318" } });
    expect(save).toBeDisabled();
    fireEvent.change(screen.getByLabelText("OTLP endpoint"), { target: { value: "http://localhost:4318" } });
    fireEvent.click(screen.getByRole("button", { name: "Send a test span" }));
    expect(await screen.findByText(/took a test span in 12 ms/)).toBeInTheDocument();
    expect(m(Setup.saveTelemetry).mock.calls[0][0].body).toEqual({ enabled: false, endpoint: "http://localhost:4318" });
    fireEvent.click(save);
    await waitFor(() => expect(Setup.finish).toHaveBeenCalled());
    expect(m(Setup.saveTelemetry).mock.calls[1][0].body).toEqual({ enabled: true, endpoint: "http://localhost:4318" });
  });

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

  it("can skip the whole wizard", async () => {
    m(Setup.getSetup).mockImplementation(() => ok(VIEW()));
    m(Setup.finish).mockImplementation(() => ok({ ok: true }));
    wrap(<SetupWizard />);
    fireEvent.click(await screen.findByRole("button", { name: "Skip setup" }));
    await waitFor(() =>
      expect(Setup.finish).toHaveBeenCalledWith(expect.objectContaining({ body: { skipped: true } })),
    );
  });
});
