import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Admin } from "@/app/openapi-client";
import { type SettingsView } from "@/components/settings/model";
import { SettingsSection } from "@/components/settings/section";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Admin: { updateSettings: jest.fn(), bridgeStatus: jest.fn(), testBridge: jest.fn() },
  Metadata: {},
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/lib/hooks/session", () => ({ useArchive: () => ({ namespaces: [] }) }));
jest.mock("@/components/ui/toast", () => ({ useToast: () => jest.fn() }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const BRIDGE = {
  enabled: true,
  url: null,
  token: { set: false },
  account: null,
  name: "Lens",
  answer: "mention",
  gateway: null,
  users: [],
  rooms: [],
  poll_seconds: 2,
};
const NETWORKS = {
  ...Object.fromEntries(["slack", "discord", "telegram"].map((n) => [`${n}_token`, { set: false }])),
  matrix_password: { set: false },
  ...Object.fromEntries(
    ["slack_channels", "discord_channels", "telegram_chats", "matrix_rooms", "whatsapp_groups"].map((k) => [k, []]),
  ),
  discord_server: null,
  matrix_server: null,
  matrix_login: null,
  whatsapp_number: null,
};

function show(run: boolean) {
  const view = {
    bridge: { values: BRIDGE },
    matterbridge: { values: { run, ...NETWORKS } },
  } as unknown as SettingsView;
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <SettingsSection id="bridge" view={view} onErrors={() => undefined} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

describe("Settings › Chat rooms › Run Matterbridge here", () => {
  beforeEach(() => {
    (Admin.updateSettings as jest.Mock).mockReset().mockImplementation(() => ok({ ok: true }));
    (Admin.bridgeStatus as jest.Mock).mockReset();
  });

  it("asks for a Matterbridge address only when Lens doesn't run one", async () => {
    (Admin.bridgeStatus as jest.Mock).mockImplementation(() => ok({ state: "incomplete", error: "x" }));
    show(false);
    expect(await screen.findByLabelText("Matterbridge API address")).toBeInTheDocument();
    expect(screen.queryByText("Slack")).not.toBeInTheDocument();
  });

  it("adds a chat network, and shows WhatsApp's QR code and the rooms", async () => {
    (Admin.bridgeStatus as jest.Mock).mockImplementation(() =>
      ok({
        state: "running",
        matterbridge: {
          run: true,
          gateways: [{ gateway: "whatsapp-family", network: "whatsapp", room: "Family" }],
          whatsapp_qr: "█▀▀█\n█▄▄█",
          error: null,
        },
      }),
    );
    show(true);
    expect(await screen.findByLabelText("WhatsApp QR code")).toHaveTextContent("█▀▀█");
    expect(screen.getByText("whatsapp-family")).toBeInTheDocument();
    expect(screen.queryByLabelText("Matterbridge API address")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Slack channels"), { target: { value: "general\nops" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(Admin.updateSettings).toHaveBeenCalled());
    const calls = (Admin.updateSettings as jest.Mock).mock.calls.map((c) => c[0]);
    expect(calls).toContainEqual(
      expect.objectContaining({ path: { section: "matterbridge" }, body: { slack_channels: ["general", "ops"] } }),
    );
  });
});
