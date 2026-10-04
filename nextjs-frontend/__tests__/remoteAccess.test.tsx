import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Admin } from "@/app/openapi-client";
import { crossErrors, type SettingsView } from "@/components/settings/model";
import { SettingsSection } from "@/components/settings/section";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Admin: { updateSettings: jest.fn(), tunnelStatus: jest.fn() },
  Metadata: {},
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/lib/hooks/session", () => ({ useArchive: () => ({ namespaces: [] }) }));
jest.mock("@/components/ui/toast", () => ({ useToast: () => jest.fn() }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const STATUS = { running: false, connected: false, url: null, error: null, log: [], origin: "http://frontend:3000" };

function show(values: Record<string, unknown>) {
  const view = { tunnel: { values } } as unknown as SettingsView;
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <SettingsSection id="remote-access" view={view} onErrors={() => undefined} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

const OFF = { mode: "off", hostname: "", token: { set: false }, api_token: { set: false }, origin: "" };

describe("Settings › Remote access", () => {
  beforeEach(() => {
    (Admin.updateSettings as jest.Mock).mockReset().mockImplementation(() => ok({ ok: true }));
    (Admin.tunnelStatus as jest.Mock).mockReset();
  });

  it("shows the address once the tunnel is connected", async () => {
    (Admin.tunnelStatus as jest.Mock).mockImplementation(() =>
      ok({ ...STATUS, mode: "quick", running: true, connected: true, url: "https://tiny-blue-fox.trycloudflare.com" }),
    );
    show({ ...OFF, mode: "quick" });
    expect(await screen.findByText("Reachable from anywhere.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "https://tiny-blue-fox.trycloudflare.com" })).toBeInTheDocument();
  });

  it("shows why the tunnel isn't up", async () => {
    (Admin.tunnelStatus as jest.Mock).mockImplementation(() =>
      ok({ ...STATUS, mode: "token", error: "cloudflared stopped (exit 1); bad token", log: ["ERR bad token"] }),
    );
    show({ ...OFF, mode: "token", hostname: "lens.example.com", token: { set: true } });
    expect(await screen.findByText("cloudflared stopped (exit 1); bad token")).toBeInTheDocument();
    expect(screen.getByText("What cloudflared said")).toBeInTheDocument();
  });

  it("sets up a tunnel on your domain with an API token", async () => {
    (Admin.tunnelStatus as jest.Mock).mockImplementation(() => ok({ ...STATUS, mode: "off" }));
    show(OFF);
    expect(await screen.findByText("Off.")).toBeInTheDocument();
    expect(screen.queryByLabelText("Public hostname")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("Your domain"));
    fireEvent.change(screen.getByLabelText("Public hostname"), { target: { value: "lens.example.com" } });
    fireEvent.change(screen.getByPlaceholderText("Paste a Cloudflare API token"), { target: { value: "cf-api" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(Admin.updateSettings).toHaveBeenCalledTimes(1));
    expect((Admin.updateSettings as jest.Mock).mock.calls[0][0]).toMatchObject({
      path: { section: "tunnel" },
      body: { mode: "managed", hostname: "lens.example.com", api_token: "cf-api" },
    });
  });

  it("checks the hostname and the web app's address", () => {
    expect(crossErrors({ "tunnel.mode": "managed", "tunnel.hostname": "" })["tunnel.hostname"]).toMatch(/hostname/);
    expect(crossErrors({ "tunnel.mode": "token", "tunnel.hostname": "lens.example.com" })).toEqual({});
    expect(crossErrors({ "tunnel.mode": "quick", "tunnel.hostname": "" })).toEqual({});
    expect(crossErrors({ "tunnel.origin": "frontend:3000" })["tunnel.origin"]).toMatch(/http/);
  });
});
