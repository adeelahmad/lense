import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Scaling } from "@/app/openapi-client";
import type { Group, Provider } from "@/app/openapi-client/types.gen";
import { groupState, termsText } from "@/components/settings/scaling-panel";
import { SettingsSection } from "@/components/settings/section";
import type { SettingsView } from "@/components/settings/model";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Admin: { updateSettings: jest.fn() },
  Scaling: { listGroups: jest.fn(), listProviders: jest.fn(), approveGroup: jest.fn(), pauseGroup: jest.fn() },
  Metadata: {},
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/lib/hooks/session", () => ({ useArchive: () => ({ namespaces: [] }) }));
jest.mock("@/components/ui/toast", () => ({ useToast: () => jest.fn() }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const VIEW = {
  scaling: { values: { enabled: false, tick_seconds: 30, join_minutes: 15, accrue_minutes: 5, tag: "lens" } },
} as unknown as SettingsView;
const PROVIDERS = [
  { id: 1, name: "Here", driver: "local", priced: false, settings: {}, secrets: {} },
  { id: 2, name: "Cloud", driver: "fake", priced: true, settings: {}, secrets: { api_key: true } },
] as Provider[];
const GROUP = {
  id: 7,
  name: "Transcribers",
  kind: "node",
  provider: 2,
  template: {},
  min: 0,
  max: 3,
  desired: 0,
  terminate: "oldest",
  rules: [],
  at_cap: "stop",
  enabled: true,
  paused: false,
  approved: false,
  terms: {},
  budget: { usd: 20, period: "month", state: "ok", spent: {}, left: {} },
  units: { launching: 0, running: 0, draining: 0 },
  waiting_for: "a person's approval of its terms, in Settings › Auto scaling",
  failures: 0,
} as Group;

function show() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <SettingsSection id="scaling" view={VIEW} onErrors={() => undefined} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  (Scaling.listProviders as jest.Mock).mockImplementation(() => ok(PROVIDERS));
});

describe("auto scaling settings", () => {
  it("shows each group's terms and approves them in one click", async () => {
    (Scaling.listGroups as jest.Mock).mockImplementation(() => ok([GROUP]));
    (Scaling.approveGroup as jest.Mock).mockImplementation(() => ok({ ...GROUP, approved: true }));
    show();
    expect(screen.getByText("Scale workers")).toBeInTheDocument();
    expect(await screen.findByText("Up to 3 units on Cloud, up to $20 a month")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Approve Transcribers/ }));
    await waitFor(() => expect(Scaling.approveGroup).toHaveBeenCalled());
    expect((Scaling.approveGroup as jest.Mock).mock.calls[0][0].path).toEqual({ gid: 7 });
  });

  it("says when there are no groups yet", async () => {
    (Scaling.listGroups as jest.Mock).mockImplementation(() => ok([]));
    show();
    expect(await screen.findByText(/No scaling groups yet/)).toBeInTheDocument();
  });

  it("puts the terms and the state in words", () => {
    expect(termsText({ ...GROUP, provider: 1, budget: null, max: 1 }, PROVIDERS)).toBe(
      "Up to 1 unit on Here, at no cost",
    );
    expect(termsText({ ...GROUP, at_cap: "min", min: 1 }, PROVIDERS)).toBe(
      "Up to 3 units on Cloud, up to $20 a month (at the cap, 1 keep running)",
    );
    expect(groupState({ ...GROUP, waiting_for: null, units: { launching: 1, running: 2, draining: 0 } })).toBe(
      "2 of 3 running, 1 joining",
    );
  });
});
