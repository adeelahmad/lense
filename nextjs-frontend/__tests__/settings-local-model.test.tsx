import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Admin } from "@/app/openapi-client";
import { SettingsSection } from "@/components/settings/section";
import type { SettingsView } from "@/components/settings/model";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Admin: { updateSettings: jest.fn(), localLlmStatus: jest.fn(), removeLocalModel: jest.fn() },
  Metadata: {},
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/lib/hooks/session", () => ({ useArchive: () => ({ namespaces: [] }) }));
jest.mock("@/components/ui/toast", () => ({ useToast: () => jest.fn() }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const VIEW = {
  local_llm: {
    values: {
      enabled: false,
      model: null,
      use_as_provider: true,
      context: 4096,
      threads: null,
      gpu_layers: 999,
      port: 8091,
      host: null,
    },
  },
} as unknown as SettingsView;
const model = (id: string, label: string, fits: boolean, downloaded = false) => ({
  id,
  label,
  repo: `acme/${id}`,
  file: `${id}.gguf`,
  license: "apache-2.0",
  about: "test",
  tools: true,
  size_gb: fits ? 1.0 : 9.0,
  memory_gb: fits ? 2.2 : 12.0,
  downloaded,
  fits,
  room: true,
});
const STATUS = {
  enabled: false,
  model: null,
  phase: "off",
  progress: null,
  url: null,
  error: null,
  log: [],
  process: null,
  server: null,
  catalog: [model("small", "Small 1B", true, true), model("big", "Big 14B", false)],
  files: [{ path: "acme__small/small.gguf", size_gb: 1.0 }],
  machine: { memory_gb: 4, disk_free_gb: 20, cpus: 4 },
};

function show() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <SettingsSection id="local-model" view={VIEW} onErrors={() => undefined} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

describe("local model", () => {
  beforeEach(() => {
    (Admin.updateSettings as jest.Mock).mockReset().mockImplementation(() => ok({ ok: true }));
    (Admin.localLlmStatus as jest.Mock).mockReset().mockImplementation(() => ok(STATUS));
    (Admin.removeLocalModel as jest.Mock).mockReset().mockImplementation(() => ok({ removed: true }));
  });

  it("offers the models that fit, and saves the pick", async () => {
    show();
    const small = await screen.findByRole("button", { name: /Small 1B/ });
    expect(screen.getByRole("button", { name: /Big 14B/ })).toBeDisabled();
    expect(screen.getByText(/4.0 GB memory, 20 GB free disk, 4 CPUs/)).toBeInTheDocument();
    fireEvent.click(small);
    expect(small).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(Admin.updateSettings).toHaveBeenCalledTimes(1));
    expect((Admin.updateSettings as jest.Mock).mock.calls[0][0]).toMatchObject({
      path: { section: "local_llm" },
      body: { model: "small" },
    });
  });

  it("deletes a downloaded model", async () => {
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
    await waitFor(() => expect(Admin.removeLocalModel).toHaveBeenCalledTimes(1));
    expect((Admin.removeLocalModel as jest.Mock).mock.calls[0][0]).toMatchObject({ query: { model: "small" } });
  });
});
