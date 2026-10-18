import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Mesh, Updates } from "@/app/openapi-client";
import { machineText, seenText, tokenTerms } from "@/components/settings/mesh-nodes";
import { crossErrors, type SettingsView } from "@/components/settings/model";
import { SettingsSection } from "@/components/settings/section";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Admin: { updateSettings: jest.fn() },
  Metadata: {},
  Mesh: {
    meshStatus: jest.fn(),
    meshNodes: jest.fn(),
    meshTokens: jest.fn(),
    makeMeshToken: jest.fn(),
    changeNode: jest.fn(),
    removeNode: jest.fn(),
    restoreNode: jest.fn(),
    withdrawMeshToken: jest.fn(),
  },
  Updates: { listServices: jest.fn() },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/lib/hooks/session", () => ({
  useArchive: () => ({ namespaces: [{ name: "podcasts" }, { name: "calls" }] }),
}));
jest.mock("@/components/ui/toast", () => ({ useToast: () => jest.fn() }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const refused = (detail: string) =>
  Promise.resolve({ error: { detail }, response: { ok: false, status: 400, statusText: "Bad Request" } });

const later = new Date(Date.now() + 20 * 86400_000).toISOString();
const PRIMARY = { id: 1, name: "This machine", role: "primary", ip: "10.73.0.1", namespaces: [], status: {} };
const GPU = {
  id: 2,
  name: "mac-studio",
  role: "worker",
  ip: "10.73.0.2",
  namespaces: [],
  not_after: later,
  last_seen: new Date(Date.now() - 2 * 60_000).toISOString(),
  status: {
    nebula: "running",
    version: "0.14.0",
    machine: { os: "Darwin", arch: "arm64", cpus: 12, memory: 32 * 1024 ** 3 },
    log: ["07:00:01 started lens worker mac-studio (transcribe,diarize)"],
  },
  workers: [],
  revoked_at: null,
};
let rows: Record<string, unknown>[] = [];

function show(id: "nodes" | "network", view: SettingsView = {} as SettingsView) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <SettingsSection id={id} view={view} onErrors={() => undefined} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

beforeAll(() => {
  // the namespace checkboxes measure themselves
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});

beforeEach(() => {
  rows = [PRIMARY, GPU];
  (Mesh.meshNodes as jest.Mock).mockReset().mockImplementation(() => ok(rows));
  (Mesh.meshTokens as jest.Mock).mockReset().mockImplementation(() => ok([]));
  (Mesh.makeMeshToken as jest.Mock).mockReset();
  (Mesh.changeNode as jest.Mock).mockReset();
  (Mesh.removeNode as jest.Mock).mockReset();
  (Mesh.restoreNode as jest.Mock).mockReset();
  (Mesh.meshStatus as jest.Mock).mockReset();
  (Updates.listServices as jest.Mock).mockReset().mockImplementation(() => ok({ services: [] }));
});

describe("Settings › Nodes", () => {
  it("adds a node with Enter and shows the install line once, with its terms", async () => {
    const line =
      "curl -fsSL https://raw.githubusercontent.com/adeelahmad/lense/main/install.sh | LENS_JOIN=http://lens.home:3000 LENS_JOIN_TOKEN=lensjoin1.7.abc sh";
    const created = new Date().toISOString();
    (Mesh.makeMeshToken as jest.Mock).mockImplementation(() =>
      ok({
        token: {
          id: 7,
          role: "compute",
          name: "sandbox-box",
          namespaces: ["calls"],
          uses: 1,
          used: 0,
          created_at: created,
          expires_at: new Date(Date.parse(created) + 24 * 3.6e6).toISOString(),
        },
        secret: "lensjoin1.7.abc",
        join_url: "http://lens.home:3000",
        install_line: line,
      }),
    );
    show("nodes");
    const name = screen.getByPlaceholderText("the machine’s own name");
    expect(name).toHaveFocus();
    fireEvent.click(screen.getByRole("radio", { name: /Compute/ }));
    fireEvent.change(name, { target: { value: "sandbox-box" } });
    fireEvent.click(within(screen.getByRole("group", { name: "Namespaces it serves" })).getByLabelText("calls"));
    fireEvent.submit(name.closest("form")!);
    expect(await screen.findByText(line)).toBeInTheDocument();
    expect(screen.getByText(/Run it on the machine; it shows here when it joins/)).toBeInTheDocument();
    expect(screen.getByText(/works once, for 24 hours/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy the install line" })).toBeInTheDocument();
    expect((Mesh.makeMeshToken as jest.Mock).mock.calls[0][0]).toMatchObject({
      body: { role: "compute", name: "sandbox-box", namespaces: ["calls"] },
    });
  });

  it("shows why a worker node is refused on the embedded database", async () => {
    (Mesh.makeMeshToken as jest.Mock).mockImplementation(() =>
      refused(
        "a worker on another machine needs this install's database to be a SurrealDB server (Docker installs have one); add a compute node instead",
      ),
    );
    show("nodes");
    fireEvent.submit(screen.getByPlaceholderText("the machine’s own name").closest("form")!);
    expect(
      await screen.findByText(/A worker on another machine needs .* add a compute node instead/),
    ).toBeInTheDocument();
  });

  it("lists each machine with how it's doing", async () => {
    show("nodes");
    const row = await screen.findByRole("listitem", { name: "mac-studio" });
    expect(within(row).getByText("Worker")).toBeInTheDocument();
    expect(within(row).getByText(/Online · seen 2 minutes ago/)).toBeInTheDocument();
    expect(within(row).getByText("10.73.0.2")).toBeInTheDocument();
    expect(within(row).getByText(/Lens 0.14.0/)).toBeInTheDocument();
    expect(within(row).getByText(/12 CPUs · 32 GB · Darwin arm64/)).toBeInTheDocument();
    expect(within(row).getByText("What Nebula and its worker said")).toBeInTheDocument();
    expect(within(row).getByText(/started lens worker mac-studio/)).toBeInTheDocument();
    // this machine first, and it can't be removed
    const primary = screen.getByRole("listitem", { name: "This machine" });
    expect(within(primary).queryByRole("button", { name: "Remove" })).not.toBeInTheDocument();
  });

  it("removes a node without asking, and undoes it", async () => {
    (Mesh.removeNode as jest.Mock).mockImplementation(() => {
      rows = [PRIMARY, { ...GPU, revoked_at: new Date().toISOString(), revoked_by: "admin@lens" }];
      return ok(rows[1]);
    });
    (Mesh.restoreNode as jest.Mock).mockImplementation(() => {
      rows = [PRIMARY, GPU];
      return ok(GPU);
    });
    show("nodes");
    const row = await screen.findByRole("listitem", { name: "mac-studio" });
    fireEvent.click(within(row).getByRole("button", { name: "Remove" }));
    expect(await within(row).findByText("Removed")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect((Mesh.removeNode as jest.Mock).mock.calls[0][0]).toMatchObject({ path: { node_id: 2 } });
    fireEvent.click(within(row).getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(within(row).queryByText("Removed")).not.toBeInTheDocument());
    expect((Mesh.restoreNode as jest.Mock).mock.calls[0][0]).toMatchObject({ path: { node_id: 2 } });
    expect(within(row).getByRole("button", { name: "Remove" })).toBeInTheDocument();
  });

  it("renames a node with Enter", async () => {
    (Mesh.changeNode as jest.Mock).mockImplementation(({ body }: { body: { name: string } }) => {
      rows = [PRIMARY, { ...GPU, name: body.name }];
      return ok(rows[1]);
    });
    show("nodes");
    const row = await screen.findByRole("listitem", { name: "mac-studio" });
    fireEvent.click(within(row).getByRole("button", { name: "Rename node" }));
    const input = within(row).getByRole("textbox", { name: "Node name" });
    fireEvent.change(input, { target: { value: "gpu-box" } });
    fireEvent.submit(input.closest("form")!);
    expect(await screen.findByRole("listitem", { name: "gpu-box" })).toBeInTheDocument();
    expect((Mesh.changeNode as jest.Mock).mock.calls[0][0]).toMatchObject({
      path: { node_id: 2 },
      body: { name: "gpu-box" },
    });
  });

  it("says when a node was last seen, and its machine", () => {
    const now = Date.parse("2026-10-18T12:00:00Z");
    expect(seenText("2026-10-18T11:58:00Z", now)).toEqual({ online: true, text: "Online · seen 2 minutes ago" });
    expect(seenText("2026-10-18T11:50:00Z", now).online).toBe(false);
    expect(seenText(null, now).text).toBe("Not seen yet");
    expect(machineText({ cpus: 4, memory: 8 * 1024 ** 3, arch: "aarch64", os: "Linux" })).toBe(
      "4 CPUs · 8 GB · Linux aarch64",
    );
    expect(
      tokenTerms({
        id: 1,
        role: "worker",
        uses: 3,
        created_at: "2026-10-18T12:00:00Z",
        expires_at: "2026-10-25T12:00:00Z",
      }),
    ).toBe("works 3 times, for 7 days");
  });
});

const MESH = {
  enabled: false,
  network: "10.73.0.0/16",
  port: 4242,
  hosts: [],
  cipher: "aes",
  cert_days: 30,
  relay: true,
  mtu: 1300,
  log_level: "info",
};
const STATUS = {
  enabled: false,
  network: "10.73.0.0/16",
  port: 4242,
  hosts: [],
  primary_ip: "10.73.0.1",
  ca: null,
  nodes: 0,
  removed: 0,
  tokens: 0,
  nebula: "1.9.5",
  tools: false,
  service: {
    available: true,
    state: null,
    nebula: false,
    restarts: 0,
    relays: [],
    log: [],
    seen_at: null,
    manual: "docker compose -f docker-compose.prod.yml --profile mesh up -d",
  },
};

describe("Settings › Network", () => {
  it("is off until a node is added", async () => {
    (Mesh.meshStatus as jest.Mock).mockImplementation(() => ok(STATUS));
    show("network", { mesh: { values: MESH } } as unknown as SettingsView);
    expect(await screen.findByText(/Off until you add a node: a single machine needs no mesh/)).toBeInTheDocument();
    expect(screen.getByText("Made when you add the first node.")).toBeInTheDocument();
    expect(screen.getByText("Run the mesh")).toBeInTheDocument();
  });

  it("gives the line that starts the mesh container where there's no upgrade agent", async () => {
    (Mesh.meshStatus as jest.Mock).mockImplementation(() =>
      ok({
        ...STATUS,
        enabled: true,
        nodes: 1,
        ca: { fingerprint: "ab12cd34", not_after: later },
      }),
    );
    (Updates.listServices as jest.Mock).mockImplementation(() =>
      ok({ services: [{ profile: "mesh", agent: null, manual: STATUS.service.manual }] }),
    );
    show("network", { mesh: { values: { ...MESH, enabled: true } } } as unknown as SettingsView);
    expect(await screen.findByText("On, but the mesh container isn’t running.")).toBeInTheDocument();
    expect(screen.getByText(STATUS.service.manual)).toBeInTheDocument();
    expect(screen.getByText("ab12cd34")).toBeInTheDocument();
  });

  it("shows that it runs, and what Nebula said", async () => {
    (Mesh.meshStatus as jest.Mock).mockImplementation(() =>
      ok({
        ...STATUS,
        enabled: true,
        nodes: 2,
        service: {
          ...STATUS.service,
          state: "running",
          nebula: true,
          seen_at: new Date().toISOString(),
          log: ['level=info msg="Nebula interface is active"'],
        },
      }),
    );
    show("network", { mesh: { values: { ...MESH, enabled: true } } } as unknown as SettingsView);
    expect(await screen.findByText("On, and running.")).toBeInTheDocument();
    expect(screen.getByText(/with 2 nodes/)).toBeInTheDocument();
    expect(screen.getByText("What Nebula said")).toBeInTheDocument();
  });

  it("checks the network and the hosts as the server does", () => {
    expect(crossErrors({ "mesh.network": "10.73.0.0/16", "mesh.hosts": ["lens.example.com:4242"] })).toEqual({});
    expect(crossErrors({ "mesh.network": "10.73.0.0/30" })["mesh.network"]).toMatch(/\/8 to \/28/);
    expect(crossErrors({ "mesh.hosts": ["bad host"] })["mesh.hosts"]).toMatch(/isn’t a host name/);
  });
});
