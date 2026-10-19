import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Scaling } from "@/app/openapi-client";
import type { DriverSpec, Group, Provider } from "@/app/openapi-client/types.gen";
import { groupState, termsText } from "@/components/settings/scaling-panel";
import { SettingsSection } from "@/components/settings/section";
import type { SettingsView } from "@/components/settings/model";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Admin: { updateSettings: jest.fn() },
  Scaling: {
    listGroups: jest.fn(),
    listProviders: jest.fn(),
    listDrivers: jest.fn(),
    addProvider: jest.fn(),
    checkProvider: jest.fn(),
    removeProvider: jest.fn(),
    letGoStray: jest.fn(),
    createGroup: jest.fn(),
    approveGroup: jest.fn(),
    pauseGroup: jest.fn(),
  },
  Metadata: {},
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/lib/hooks/session", () => ({ useArchive: () => ({ namespaces: [] }) }));
const mockToast = jest.fn();
jest.mock("@/components/ui/toast", () => ({ useToast: () => mockToast }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const VIEW = {
  scaling: { values: { enabled: false, tick_seconds: 30, join_minutes: 15, accrue_minutes: 5, tag: "lens" } },
} as unknown as SettingsView;
const PROVIDERS = [
  { id: 1, name: "Here", driver: "local", priced: false, settings: {}, secrets: {} },
  { id: 2, name: "Cloud", driver: "fake", priced: true, settings: {}, secrets: { api_key: true } },
] as Provider[];
const DRIVERS = [
  {
    id: "local",
    kind: "node",
    label: "This machine",
    about: "Runs more Lens workers here.",
    priced: false,
    ready: true,
    fields: {},
    template: { steps: { label: "Steps", type: "list", options: ["embed", "transcribe"] } },
  },
  {
    id: "fake",
    kind: "node",
    label: "Fake cloud",
    about: "Machines on a pretend cloud.",
    priced: true,
    extra: "fakecloud",
    license: "MIT",
    ready: false,
    fields: {
      region: { label: "Region", type: "string", required: true, default: "ams3" },
      token: { label: "API token", type: "string", required: true, secret: true },
      private: { label: "Private network", type: "boolean", default: true },
    },
    template: { size: { label: "Size", type: "string", default: "small" } },
  },
] as DriverSpec[];
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
  jest.clearAllMocks();
  (Scaling.listProviders as jest.Mock).mockImplementation(() => ok(PROVIDERS));
  (Scaling.listDrivers as jest.Mock).mockImplementation(() => ok(DRIVERS));
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

  it("adds a provider from its driver's fields, with the secret sent apart", async () => {
    (Scaling.listGroups as jest.Mock).mockImplementation(() => ok([]));
    (Scaling.addProvider as jest.Mock).mockImplementation(({ body }) =>
      ok({ id: 3, ...body, priced: true, secrets: { token: true }, check: { ok: true } }),
    );
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Add a provider" }));
    fireEvent.click(await screen.findByRole("button", { name: /Fake cloud/ }));
    expect(screen.getByText(/Needs the fakecloud packages \(MIT\)/)).toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toHaveValue("Fake cloud");
    expect(screen.getByLabelText("Name")).toHaveFocus();
    expect(screen.getByLabelText(/Region/)).toHaveValue("ams3");
    const token = screen.getByLabelText(/API token/);
    expect(token).toHaveAttribute("type", "password");
    fireEvent.change(token, { target: { value: "s3cret" } });
    fireEvent.keyDown(token, { key: "Enter" });
    await waitFor(() => expect(Scaling.addProvider).toHaveBeenCalled());
    expect((Scaling.addProvider as jest.Mock).mock.calls[0][0].body).toEqual({
      name: "Fake cloud",
      driver: "fake",
      settings: { region: "ams3", private: true },
      secrets: { token: "s3cret" },
    });
    await waitFor(() => expect(mockToast).toHaveBeenCalledWith(expect.objectContaining({ title: "Added Fake cloud" })));
  });

  it("shows a failed check in words, after saving and in the list", async () => {
    (Scaling.listGroups as jest.Mock).mockImplementation(() => ok([]));
    (Scaling.listProviders as jest.Mock).mockImplementation(() =>
      ok([{ ...PROVIDERS[1], secrets: { token: true }, check: { ok: false, error: "the token was refused" } }]),
    );
    (Scaling.addProvider as jest.Mock).mockImplementation(({ body }) =>
      ok({
        id: 3,
        ...body,
        priced: true,
        secrets: { token: true },
        check: { ok: false, error: "the region is unknown" },
      }),
    );
    (Scaling.checkProvider as jest.Mock).mockImplementation(() => ok({ ok: false, error: "the token was refused" }));
    show();
    expect(await screen.findByText("Check failed: The token was refused.")).toBeInTheDocument();
    expect(screen.getByText(/API token set/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Check Cloud again" }));
    await waitFor(() => expect(Scaling.checkProvider).toHaveBeenCalled());
    expect((Scaling.checkProvider as jest.Mock).mock.calls[0][0].path).toEqual({ pid: 2 });

    fireEvent.click(screen.getByRole("button", { name: "Add a provider" }));
    fireEvent.click(await screen.findByRole("button", { name: /Fake cloud/ }));
    fireEvent.click(screen.getByRole("button", { name: "Add provider" }));
    expect(await screen.findByText("API token is needed.")).toBeInTheDocument();
    expect(Scaling.addProvider).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText(/API token/), { target: { value: "s3cret" } });
    fireEvent.click(screen.getByRole("button", { name: "Add provider" }));
    expect(await screen.findByText("It’s saved, but its check failed.")).toBeInTheDocument();
    expect(screen.getByText(/The region is unknown\./)).toBeInTheDocument();
  });

  it("shows the units a provider runs that Lens lost track of, and why it couldn't ask", async () => {
    (Scaling.listGroups as jest.Mock).mockImplementation(() => ok([]));
    (Scaling.listProviders as jest.Mock).mockImplementation(() =>
      ok([
        { ...PROVIDERS[0], list_error: "the host didn't answer", strays: [] },
        {
          ...PROVIDERS[1],
          strays: [
            { id: "101", name: "lens-workers-4", seen_at: "2026-10-19T03:00:00+00:00" },
            { id: "102", seen_at: "2026-10-19T03:00:00+00:00" },
          ],
        },
      ]),
    );
    show();
    expect(
      await screen.findByText(/Runs 2 units with Lens’s tag that Lens didn’t keep track of\./),
    ).toBeInTheDocument();
    expect(screen.getByText("Couldn’t list its units: The host didn't answer.")).toBeInTheDocument();
    expect(screen.getByText("lens-workers-4")).toBeInTheDocument();
    expect(screen.getByText("102")).toBeInTheDocument();
  });

  it("lets a stray go with Undo instead of a question", async () => {
    (Scaling.listGroups as jest.Mock).mockImplementation(() => ok([]));
    (Scaling.listProviders as jest.Mock).mockImplementation(() =>
      ok([{ ...PROVIDERS[1], strays: [{ id: "101", name: "lens-workers-4" }] }]),
    );
    (Scaling.letGoStray as jest.Mock).mockImplementation(() => ok({ ...PROVIDERS[1], strays: [] }));
    const { unmount } = show();
    fireEvent.click(await screen.findByRole("button", { name: "Let lens-workers-4 go" }));
    expect(screen.queryByText("lens-workers-4")).not.toBeInTheDocument();
    const undo = mockToast.mock.calls[mockToast.mock.calls.length - 1][0];
    expect(undo.title).toBe("Letting lens-workers-4 go at Cloud");
    act(() => undo.action.onClick());
    expect(screen.getByText("lens-workers-4")).toBeInTheDocument();
    expect(Scaling.letGoStray).not.toHaveBeenCalled();
    // leaving the page lets go what waits for Undo at once
    fireEvent.click(screen.getByRole("button", { name: "Let lens-workers-4 go" }));
    unmount();
    expect((Scaling.letGoStray as jest.Mock).mock.calls[0][0]).toMatchObject({ path: { pid: 2 }, body: { id: "101" } });
  });

  it("makes a group with a budget on a priced provider", async () => {
    (Scaling.listGroups as jest.Mock).mockImplementation(() => ok([]));
    (Scaling.createGroup as jest.Mock).mockImplementation(({ body }) =>
      ok({ ...GROUP, ...body, budget: { ...GROUP.budget, usd: body.budget }, approved: true }),
    );
    show();
    fireEvent.click(await screen.findByRole("button", { name: "New group on Cloud" }));
    expect(screen.getByLabelText("Name")).toHaveValue("Cloud workers");
    expect(screen.getByLabelText("Minimum")).toHaveValue(0);
    expect(screen.getByLabelText("Maximum")).toHaveValue(1);
    expect(screen.getByLabelText(/Size/)).toHaveValue("small");
    expect(screen.getByText(/set by the assistant, MCP or the API for now/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Make group" }));
    expect(await screen.findByText(/A budget is needed/)).toBeInTheDocument();
    expect(Scaling.createGroup).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Budget, USD a month"), { target: { value: "25" } });
    fireEvent.change(screen.getByLabelText("Maximum"), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "Make group" }));
    await waitFor(() => expect(Scaling.createGroup).toHaveBeenCalled());
    expect((Scaling.createGroup as jest.Mock).mock.calls[0][0].body).toEqual({
      name: "Cloud workers",
      provider: 2,
      min: 0,
      max: 3,
      template: { size: "small" },
      budget: 25,
    });
  });

  it("asks no budget on a free provider and ticks a list's options", async () => {
    (Scaling.listGroups as jest.Mock).mockImplementation(() => ok([]));
    (Scaling.createGroup as jest.Mock).mockImplementation(({ body }) => ok({ ...GROUP, ...body, budget: null }));
    show();
    fireEvent.click(await screen.findByRole("button", { name: "New group on Here" }));
    expect(screen.queryByLabelText("Budget, USD a month")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: "transcribe" }));
    fireEvent.keyDown(screen.getByLabelText("Name"), { key: "Enter" });
    await waitFor(() => expect(Scaling.createGroup).toHaveBeenCalled());
    expect((Scaling.createGroup as jest.Mock).mock.calls[0][0].body).toEqual({
      name: "Here workers",
      provider: 1,
      min: 0,
      max: 1,
      template: { steps: ["transcribe"] },
    });
  });

  it("removes a provider with Undo instead of a question", async () => {
    (Scaling.listGroups as jest.Mock).mockImplementation(() => ok([GROUP]));
    (Scaling.removeProvider as jest.Mock).mockImplementation(() => ok({ ok: true }));
    const { unmount } = show();
    fireEvent.click(await screen.findByRole("button", { name: "Remove Here" }));
    expect(screen.queryByText("Here")).not.toBeInTheDocument();
    const undo = mockToast.mock.calls[mockToast.mock.calls.length - 1][0];
    expect(undo.title).toBe("Removed Here");
    act(() => undo.action.onClick());
    expect(screen.getByText("Here")).toBeInTheDocument();
    expect(Scaling.removeProvider).not.toHaveBeenCalled();
    // Cloud runs Transcribers, so it can't go yet
    expect(screen.getByRole("button", { name: "Remove Cloud" })).toHaveAttribute("aria-disabled", "true");
    // leaving the page removes what waits for Undo at once
    fireEvent.click(screen.getByRole("button", { name: "Remove Here" }));
    unmount();
    expect((Scaling.removeProvider as jest.Mock).mock.calls[0][0].path).toEqual({ pid: 1 });
  });
});
