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

beforeEach(() => jest.clearAllMocks());

describe("SetupWizard", () => {
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
    expect(await screen.findByText("media")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    const base = await screen.findByLabelText("Base URL");
    expect(base).toBeDisabled();
    expect(base).toHaveValue("http://vllm:8000/v1");
    expect(base).toHaveAccessibleDescription(/LENS_LLM_BASE_URL/);
    expect(screen.getByLabelText(/API key/)).toBeDisabled();
    expect(screen.getByLabelText("Model")).toBeEnabled();
  });

  it("counts a namespace seeded before the wizard as set up", async () => {
    m(Setup.getSetup).mockImplementation(() => ok(VIEW({ namespace: { existing: ["media"], locked: false } })));
    wrap(<SetupWizard />);
    expect(await screen.findByText(/already has one/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create and continue" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByLabelText("Base URL")).toBeInTheDocument();
    expect(Setup.saveNamespace).not.toHaveBeenCalled();
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
