import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom";
import type { ReactNode } from "react";

import { Automations } from "@/app/openapi-client";
import { AutomationPage } from "@/components/automations/automation-page";
import { RunsTab, UpcomingTab, VariablesTab, runCounts, varFrom, varText } from "@/components/automations/detail-tabs";
import { ToastProvider } from "@/components/ui/toast";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Automations: {
    listActions: jest.fn(),
    listRuns: jest.fn(),
    getRun: jest.fn(),
    decideHeldRun: jest.fn(),
    upcoming: jest.fn(),
    getAutomationVars: jest.fn(),
    putAutomationVars: jest.fn(),
    getAutomation: jest.fn(),
    runAutomation: jest.fn(),
    enableAutomation: jest.fn(),
  },
}));
jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const m = (f: unknown) => f as jest.Mock;

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <ToastProvider>{ui}</ToastProvider>
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

const run = (id: number, more: Record<string, unknown> = {}) => ({
  id,
  automation: 7,
  status: "done",
  trigger: "email",
  started_at: "2026-10-18T06:00:00Z",
  counts: { done: 1 },
  ...more,
});

beforeEach(() => {
  m(Automations.listActions).mockReturnValue(
    ok([{ name: "notify", label: "Send a message", group: "Messages", params: {}, effect: "depends" }]),
  );
});

test("a run reads what it did in a few words, and a variable edits as one line of JSON", () => {
  expect(runCounts({ counts: { done: 3, waiting: 1, failed: 0 } })).toBe("3 done, 1 waiting");
  expect(runCounts({ counts: {}, why: "over its budget" })).toBe("over its budget");
  expect(runCounts({ counts: { done: 1 }, error: "boom" })).toBe("boom");
  expect(varText({ seen: 2 })).toBe('{"seen":2}');
  expect(varText("plain")).toBe("plain");
  expect(varFrom("[1, 2]")).toEqual([1, 2]);
  expect(varFrom("not json")).toBe("not json");
});

test("Runs pages back with before, and a run opens to what it did and its log", async () => {
  m(Automations.listRuns)
    .mockReturnValueOnce(
      ok({ runs: [run(9), run(8, { status: "failed", error: "The address didn’t answer" })], next: 8 }),
    )
    .mockReturnValueOnce(ok({ runs: [run(5)], next: null }));
  m(Automations.getRun).mockReturnValue(
    ok({
      ...run(9),
      log: ["notify: sent"],
      intents: [{ action: "notify", status: "done" }],
      trace: {},
    }),
  );
  wrap(<RunsTab aid={7} />);
  const list = await screen.findByRole("list", { name: "Runs" });
  expect(within(list).getByText("The address didn’t answer")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Older runs" }));
  await waitFor(() => expect(within(list).getAllByRole("listitem")).toHaveLength(3));
  expect(m(Automations.listRuns).mock.calls[1][0].query).toEqual({ before: 8, limit: 20 });
  expect(screen.queryByRole("button", { name: "Older runs" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Run 9" }));
  expect(await screen.findByText("notify: sent")).toBeInTheDocument();
  expect(
    within(screen.getByRole("list", { name: "What it did" })).getByText("Send a message: done"),
  ).toBeInTheDocument();
});

test("a run held over a budget can run once or be skipped", async () => {
  m(Automations.listRuns).mockReturnValue(ok({ runs: [run(4, { status: "held", counts: {} })], next: null }));
  m(Automations.decideHeldRun).mockReturnValue(ok({ ok: true }));
  wrap(<RunsTab aid={7} />);
  fireEvent.click(await screen.findByRole("button", { name: "Run it once" }));
  await waitFor(() =>
    expect(m(Automations.decideHeldRun).mock.calls[0][0]).toMatchObject({ path: { run: 4 }, body: { run: true } }),
  );
});

test("Coming up lists its times, and says why when there are none", async () => {
  m(Automations.upcoming).mockReturnValueOnce(
    ok({
      times: [
        { key: "sched-7-daily", at: "2026-10-19T07:00:00Z", description: "every day at 07:00", trigger: "daily" },
      ],
    }),
  );
  const { unmount } = wrap(<UpcomingTab aid={7} enabled />);
  expect(await screen.findByText("every day at 07:00")).toBeInTheDocument();
  unmount();
  m(Automations.upcoming).mockReturnValueOnce(ok({ times: [] }));
  wrap(<UpcomingTab aid={7} enabled={false} />);
  expect(await screen.findByText(/It’s off/)).toBeInTheDocument();
});

test("Variables set from the keyboard, and Clear is undone from its toast", async () => {
  const vars = { vars: [{ name: "seen", value: 2, updated_at: "2026-10-18T06:00:00Z" }] };
  m(Automations.getAutomationVars).mockReturnValue(ok(vars));
  m(Automations.putAutomationVars).mockReturnValue(ok(vars));
  wrap(<VariablesTab aid={7} />);
  const box = await screen.findByLabelText("Value of seen");
  fireEvent.change(box, { target: { value: "3" } });
  fireEvent.keyDown(box, { key: "Enter" });
  await waitFor(() =>
    expect(m(Automations.putAutomationVars).mock.calls[0][0]).toMatchObject({
      path: { aid: 7 },
      body: { vars: { seen: 3 } },
    }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Clear" }));
  await waitFor(() => expect(m(Automations.putAutomationVars).mock.calls[1][0].body).toEqual({ vars: { seen: null } }));
  fireEvent.click(await screen.findByRole("button", { name: "Undo" }));
  await waitFor(() => expect(m(Automations.putAutomationVars).mock.calls[2][0].body).toEqual({ vars: { seen: 2 } }));
  fireEvent.change(screen.getByLabelText("New variable name"), { target: { value: "trips" } });
  fireEvent.change(screen.getByLabelText("New variable value"), { target: { value: '["Rome"]' } });
  fireEvent.keyDown(screen.getByLabelText("New variable value"), { key: "Enter" });
  await waitFor(() =>
    expect(m(Automations.putAutomationVars).mock.calls[3][0].body).toEqual({ vars: { trips: ["Rome"] } }),
  );
});

test("the page opens the tab its address names, and Run now shows the runs", async () => {
  m(Automations.getAutomation).mockReturnValue(
    ok({
      id: 7,
      name: "School emails",
      enabled: true,
      triggers: [],
      join: { mode: "any" },
      graph: { nodes: [], edges: [] },
    }),
  );
  m(Automations.upcoming).mockReturnValue(ok({ times: [] }));
  m(Automations.listRuns).mockReturnValue(ok({ runs: [run(9)], next: null }));
  m(Automations.runAutomation).mockReturnValue(ok({ run: 10, status: "done" }));
  wrap(<AutomationPage id={7} tab="upcoming" />);
  expect(await screen.findByRole("tab", { name: "Coming up" })).toHaveAttribute("aria-selected", "true");
  expect(await screen.findByText("Nothing coming up")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Run now" }));
  expect(await screen.findByRole("list", { name: "Runs" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Runs" })).toHaveAttribute("aria-selected", "true");
});
