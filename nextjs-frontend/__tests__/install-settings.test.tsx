import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";
import type { ReactNode } from "react";

import { Updates } from "@/app/openapi-client";
import type { InstallStatus } from "@/app/openapi-client/types.gen";
import {
  EditionChoice,
  going,
  manualLine,
  NetworkReach,
  onTheNetwork,
  runWhat,
} from "@/components/settings/install-settings";
import type { SettingsView } from "@/components/settings/model";
import { SettingsSection } from "@/components/settings/section";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Updates: { getInstall: jest.fn(), setInstall: jest.fn() },
  Admin: { updateSettings: jest.fn() },
  Metadata: {},
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/lib/hooks/session", () => ({ useArchive: () => ({ namespaces: [] }) }));
jest.mock("@/components/ui/toast", () => ({ useToast: () => jest.fn() }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const m = (f: unknown) => f as jest.Mock;
const LINE = "curl -fsSL https://raw.githubusercontent.com/adeelahmad/lense/main/install.sh | LENS_REF=main";
const STATUS: InstallStatus = {
  public: false,
  target: "full",
  agent: "systemd",
  manual: {
    public: `${LINE} LENS_PUBLIC=1 sh`,
    private: `${LINE} LENS_PUBLIC=0 sh`,
    full: `${LINE} LENS_TARGET=full sh`,
    lean: `${LINE} LENS_TARGET=lean sh`,
  },
  reconfigure: null,
};

function show(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>{ui}</TooltipProvider>
    </QueryClientProvider>,
  );
}

const reach = () => screen.findByRole("switch", { name: "Reachable from other devices on this network" });

describe("Settings › Access & embedding: reachable from the network", () => {
  beforeEach(() => {
    m(Updates.getInstall)
      .mockReset()
      .mockImplementation(() => ok(STATUS));
    m(Updates.setInstall).mockReset();
  });

  it("says what opening it does, and opens it with one button through the upgrade agent", async () => {
    m(Updates.setInstall).mockImplementation(() =>
      ok({ ...STATUS, reconfigure: { id: "r1", ref: "", public: true, state: "queued", log: [] } }),
    );
    show(<NetworkReach />);
    expect(await reach()).not.toBeChecked();
    expect(screen.getByText(/Only this machine opens Lens/)).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument(); // nothing to apply until it's changed
    fireEvent.click(await reach());
    expect(screen.getByText("Lens restarts to listen on the network.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Open to the network and restart" }));
    expect(await screen.findByText("Opening Lens to the network.")).toBeInTheDocument();
    expect(screen.getByText("Waiting for the machine to pick it up.")).toBeInTheDocument();
    expect(m(Updates.setInstall).mock.calls[0][0]).toMatchObject({ body: { public: true } });
    expect(await reach()).toBeDisabled(); // one change at a time
  });

  it("switching it back leaves nothing to apply", async () => {
    show(<NetworkReach />);
    fireEvent.click(await reach());
    fireEvent.click(await reach());
    expect(screen.queryByText(/Lens restarts/)).not.toBeInTheDocument();
    expect(Updates.setInstall).not.toHaveBeenCalled();
  });

  it("gives the line to run when the machine has no upgrade agent", async () => {
    m(Updates.getInstall).mockImplementation(() => ok({ ...STATUS, public: true, agent: null }));
    show(<NetworkReach />);
    expect(await reach()).toBeChecked();
    fireEvent.click(await reach());
    expect(screen.getByText("Lens restarts to listen on this machine only.")).toBeInTheDocument();
    expect(screen.getByText(`${LINE} LENS_PUBLIC=0 sh`)).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("says when it can't tell, rather than guessing", async () => {
    m(Updates.getInstall).mockImplementation(() => ok({ ...STATUS, public: null, target: null }));
    show(
      <>
        <NetworkReach />
        <EditionChoice />
      </>,
    );
    expect(await screen.findAllByText(/Lens can’t tell/)).toHaveLength(2);
    expect(screen.getByRole("radio", { name: /Full/ })).toHaveAttribute("aria-checked", "false");
    expect(screen.getByRole("radio", { name: /Lean/ })).toHaveAttribute("aria-checked", "false");
  });

  it("shows why the machine couldn't change it", async () => {
    m(Updates.getInstall).mockImplementation(() =>
      ok({
        ...STATUS,
        reconfigure: {
          id: "r1",
          ref: "",
          public: true,
          state: "failed",
          error: "install.sh stopped (exit 1)",
          log: ["x"],
        },
      }),
    );
    show(<NetworkReach />);
    expect(await screen.findByText("The machine couldn’t change it.")).toBeInTheDocument();
    expect(screen.getByText("install.sh stopped (exit 1)")).toBeInTheDocument();
    expect(await reach()).toBeEnabled();
  });

  it("shows the switch in the Access & embedding section", async () => {
    const view = {
      server: {
        values: {
          allowed_hosts: ["localhost"],
          embed_frame_ancestors: [],
          trusted_proxies: [],
          session_hours: 12,
          secure_cookies: false,
          max_upload_mb: 500,
        },
      },
    } as unknown as SettingsView;
    show(<SettingsSection id="access" view={view} onErrors={() => undefined} />);
    expect(await reach()).toBeInTheDocument();
  });
});

describe("Settings › Components: the edition", () => {
  beforeEach(() => {
    m(Updates.getInstall)
      .mockReset()
      .mockImplementation(() => ok(STATUS));
    m(Updates.setInstall).mockReset();
  });

  it("switches to lean with one button, after saying what it means", async () => {
    m(Updates.setInstall).mockImplementation(() =>
      ok({ ...STATUS, reconfigure: { id: "r2", ref: "", target: "lean", state: "running", log: ["Building…"] } }),
    );
    show(<EditionChoice />);
    expect(await screen.findByRole("radio", { name: /Full/ })).toHaveAttribute("aria-checked", "true");
    fireEvent.click(screen.getByRole("radio", { name: /Lean/ }));
    expect(screen.getByText("Lens restarts as the lean edition.")).toBeInTheDocument();
    expect(screen.getByText(/other documents then need anytopdf or Tika/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Switch to lean and restart" }));
    expect(await screen.findByText("Switching to the lean edition.")).toBeInTheDocument();
    expect(screen.getByText("Building…")).toBeInTheDocument();
    await waitFor(() => expect(Updates.setInstall).toHaveBeenCalledTimes(1));
    expect(m(Updates.setInstall).mock.calls[0][0]).toMatchObject({ body: { target: "lean" } });
  });

  it("gives the line to run without an agent", async () => {
    m(Updates.getInstall).mockImplementation(() => ok({ ...STATUS, target: "lean", agent: null }));
    show(<EditionChoice />);
    fireEvent.click(await screen.findByRole("radio", { name: /Full/ }));
    expect(screen.getByText("Lens restarts as the full edition.")).toBeInTheDocument();
    expect(screen.getByText(`${LINE} LENS_TARGET=full sh`)).toBeInTheDocument();
  });

  it("shows the API's reason when the machine is busy", async () => {
    m(Updates.setInstall).mockImplementation(() =>
      Promise.resolve({
        error: { detail: "an upgrade is already under way; try again when it's done" },
        response: { ok: false, status: 409 },
      }),
    );
    show(<EditionChoice />);
    fireEvent.click(await screen.findByRole("radio", { name: /Lean/ }));
    fireEvent.click(screen.getByRole("button", { name: "Switch to lean and restart" }));
    expect(await screen.findByText(/already under way/)).toBeInTheDocument();
  });
});

describe("the helpers", () => {
  it("know a change that's still going", () => {
    expect(going({ id: "a", ref: "", state: "queued" })).toBe(true);
    expect(going({ id: "a", ref: "", state: "running" })).toBe(true);
    expect(going({ id: "a", ref: "", state: "queued", error: "not picked up" })).toBe(false);
    expect(going({ id: "a", ref: "", state: "done" })).toBe(false);
    expect(going(null)).toBe(false);
  });

  it("pick the line for each change", () => {
    expect(manualLine(STATUS, { public: true })).toMatch(/LENS_PUBLIC=1 sh$/);
    expect(manualLine(STATUS, { public: false })).toMatch(/LENS_PUBLIC=0 sh$/);
    expect(manualLine(STATUS, { target: "lean" })).toMatch(/LENS_TARGET=lean sh$/);
  });

  it("tell an address on the network from this machine's own", () => {
    expect(onTheNetwork("192.168.1.20")).toBe(true);
    expect(onTheNetwork("lens.lan")).toBe(true);
    expect(onTheNetwork("localhost")).toBe(false);
    expect(onTheNetwork("127.0.0.1")).toBe(false);
    expect(onTheNetwork("[::1]")).toBe(false);
  });

  it("say what a run changes", () => {
    expect(runWhat({ public: false })).toBe("Keeping Lens to this machine");
    expect(runWhat({ public: true, target: "full" })).toBe(
      "Opening Lens to the network and switching to the full edition",
    );
  });
});
