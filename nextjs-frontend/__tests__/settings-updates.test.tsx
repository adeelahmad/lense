import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Updates } from "@/app/openapi-client";
import { UpdatesPanel, steps } from "@/components/settings/updates-panel";

jest.mock("@/app/openapi-client", () => ({
  Updates: {
    getUpdates: jest.fn(),
    checkUpdates: jest.fn(),
    startUpgrade: jest.fn(),
    listBackups: jest.fn(),
    takeBackup: jest.fn(),
    deleteBackup: jest.fn(),
    downloadBackup: jest.fn(),
  },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const LATEST = { tag: "v0.7.0", name: "Lens v0.7.0", url: "https://x/v0.7.0", notes: "## Features\n- more" };
const STATUS = {
  current: "0.6.0",
  latest: LATEST,
  available: true,
  checked_at: "2026-10-10T12:00:00Z",
  error: null,
  check: true,
  auto_install: false,
  agent: "systemd",
  manual: "curl … | LENS_REF=v0.7.0 sh",
  upgrade: null,
};

function show() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <UpdatesPanel />
    </QueryClientProvider>,
  );
}

describe("updates", () => {
  beforeEach(() => {
    (Updates.getUpdates as jest.Mock).mockReset().mockImplementation(() => ok(STATUS));
    (Updates.listBackups as jest.Mock).mockReset().mockImplementation(() =>
      ok({
        folder: "/data/backups",
        backups: [
          {
            name: "manual-20261001-101010.surql.gz",
            label: "manual",
            kind: "export",
            bytes: 2048,
            taken_at: "2026-10-01T10:10:10+00:00",
          },
        ],
      }),
    );
    (Updates.startUpgrade as jest.Mock)
      .mockReset()
      .mockImplementation(() =>
        ok({ ...STATUS, upgrade: { id: "a", ref: "v0.7.0", state: "backing-up", by: "me@x.io", log: [] } }),
      );
  });

  it("offers the new release and installs it after a backup", async () => {
    show();
    expect(await screen.findByText("Lens v0.7.0 is out. This is 0.6.0.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Settings → Backups" })).toHaveAttribute("href", "/settings/backups");
    fireEvent.click(screen.getByRole("button", { name: "Back up and install v0.7.0" }));
    await waitFor(() =>
      expect(Updates.startUpgrade).toHaveBeenCalledWith(
        expect.objectContaining({ body: { ref: "v0.7.0", backup: true } }),
      ),
    );
    expect(await screen.findByText("Backing up")).toBeInTheDocument();
  });

  it("shows the line to run when the machine has no agent", async () => {
    (Updates.getUpdates as jest.Mock).mockImplementation(() => ok({ ...STATUS, agent: null }));
    show();
    expect(await screen.findByText("curl … | LENS_REF=v0.7.0 sh")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Back up and install/ })).not.toBeInTheDocument();
  });

  it("says when it is up to date", async () => {
    (Updates.getUpdates as jest.Mock).mockImplementation(() =>
      ok({ ...STATUS, available: false, latest: { ...LATEST, tag: "v0.6.0" } }),
    );
    show();
    expect(await screen.findByText("Lens 0.6.0 is the latest.")).toBeInTheDocument();
  });

  it("shows each step of the upgrade as the machine goes", async () => {
    const run = { id: "a", ref: "v0.7.0", state: "running", step: "build", by: "me@x.io", log: [] };
    (Updates.getUpdates as jest.Mock).mockImplementation(() => ok({ ...STATUS, upgrade: run }));
    show();
    const list = await screen.findByRole("list", { name: "Upgrade steps" });
    const states = Array.from(list.querySelectorAll("li")).map((li) => li.getAttribute("data-state"));
    expect(states).toEqual(["done", "done", "running", "pending", "pending"]);
  });

  it("says when it went back to the previous version, and what became of the database", async () => {
    const run = {
      id: "a",
      ref: "v0.7.0",
      state: "rolled-back",
      step: "check",
      error: "the new version didn't start, so Lens went back to the one that ran before (0.6.0)",
      restore: "the new version hadn't changed the database, so it was kept as it is",
      log: [],
    };
    (Updates.getUpdates as jest.Mock).mockImplementation(() => ok({ ...STATUS, upgrade: run }));
    show();
    expect(await screen.findByText("Went back")).toBeInTheDocument();
    expect(screen.getByText(/so it was kept as it is/)).toBeInTheDocument();
    expect(screen.getByText(/went back to the one that ran before/)).toBeInTheDocument();
  });
});

describe("upgrade steps", () => {
  const at = (state: string, step?: string) =>
    steps({ id: "a", ref: "v1.0.0", state, step, log: [] } as Parameters<typeof steps>[0]);
  it("follow the run", () => {
    expect(at("backing-up")).toEqual(["running", "pending", "pending", "pending", "pending"]);
    expect(at("queued")).toEqual(["done", "pending", "pending", "pending", "pending"]);
    expect(at("running")).toEqual(["done", "running", "pending", "pending", "pending"]);
    expect(at("rolling-back", "start")).toEqual(["done", "done", "done", "failed", "pending"]);
    expect(at("done")).toEqual(["done", "done", "done", "done", "done"]);
  });
  it("mark the backup failed when backing up stopped the upgrade", () => {
    expect(at("failed", "backup")).toEqual(["failed", "pending", "pending", "pending", "pending"]);
    expect(at("failed", "download")).toEqual(["done", "failed", "pending", "pending", "pending"]);
  });
});
