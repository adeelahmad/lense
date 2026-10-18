import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom";
import type { ReactNode } from "react";

import { Routines, Schedules } from "@/app/openapi-client";
import type { Schedule } from "@/app/openapi-client/types.gen";
import { SchedulesPage } from "@/components/schedules/schedules-page";
import { ToastProvider } from "@/components/ui/toast";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Schedules: { listSchedules: jest.fn(), changeSchedule: jest.fn(), runSchedule: jest.fn() },
  Routines: { previewSchedule: jest.fn() },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
let admin = true;
jest.mock("@/lib/hooks/session", () => ({ useArchive: () => ({ admin, me: { email: "root@x.io" } }) }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const m = (f: unknown) => f as jest.Mock;

function row(over: Partial<Schedule>): Schedule {
  return {
    id: "routine:3",
    kind: "routine",
    name: "Sync nightly",
    on: "schedule",
    cron: "0 3 * * *",
    timezone: "UTC",
    text: "every day at 03:00",
    enabled: true,
    state: "on",
    next_run_at: new Date(Date.now() + 3_600_000).toISOString(),
    last_run_at: new Date(Date.now() - 3_600_000).toISOString(),
    last_status: "done",
    href: "/routines/3",
    namespace_names: null,
    ...over,
  };
}

let rows: Schedule[] = [];
beforeEach(() => {
  admin = true;
  rows = [
    row({}),
    row({
      id: "automation:4:morning",
      kind: "automation",
      name: "Mornings",
      trigger: "morning",
      cron: "0 7 * * 1-5",
      timezone: "Europe/London",
      text: "every weekday at 07:00",
      state: "waiting",
      href: "/automations/4",
      namespace_names: ["pods"],
    }),
    row({
      id: "backups",
      kind: "backups",
      name: "Back up the database",
      on: "manual",
      cron: null,
      text: "not scheduled",
      enabled: false,
      state: "off",
      next_run_at: null,
      last_run_at: null,
      last_status: null,
      href: "/settings/backups",
    }),
  ];
  m(Schedules.listSchedules).mockImplementation(() => ok({ schedules: rows }));
  m(Schedules.changeSchedule).mockImplementation(
    ({ path, body }: { path: { sid: string }; body: Partial<Schedule> }) => {
      const r = rows.find((x) => x.id === path.sid)!;
      const text = body.cron === "0 9 * * 1-5" ? "every weekday at 09:00" : r.text;
      return ok({ ...r, ...body, text });
    },
  );
  m(Schedules.runSchedule).mockImplementation(({ path }: { path: { sid: string } }) =>
    ok(path.sid.startsWith("routine") ? { status: "asked" } : { status: "done", run: 51 }),
  );
  m(Routines.previewSchedule).mockImplementation(({ query }: { query: { schedule: string; timezone: string } }) =>
    ok({ schedule: query.schedule, timezone: query.timezone, text: "every weekday at 09:00", next: [] }),
  );
});

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

test("lists every routine, automation schedule and the backup, each linking to its own page", async () => {
  wrap(<SchedulesPage />);
  const table = await screen.findByRole("table", { name: "Schedules" });
  // a tab of Automations, with the Routines page and a new routine one click away
  expect(screen.getByRole("tab", { name: "Automations" })).toHaveAttribute("href", "/automations");
  expect(screen.getByRole("link", { name: "Routines" })).toHaveAttribute("href", "/routines");
  expect(screen.getByRole("link", { name: "New routine" })).toHaveAttribute("href", "/routines/new");
  expect(within(table).getByRole("link", { name: "Sync nightly" })).toHaveAttribute("href", "/routines/3");
  expect(within(table).getByRole("link", { name: "Mornings" })).toHaveAttribute("href", "/automations/4");
  expect(within(table).getByRole("link", { name: "Back up the database" })).toHaveAttribute(
    "href",
    "/settings/backups",
  );
  expect(within(table).getByText("every weekday at 07:00")).toBeInTheDocument();
  expect(within(table).getByText("Automation · morning · pods")).toBeInTheDocument();
  expect(within(table).getByText("not scheduled")).toBeInTheDocument();
  // a filter per kind, with counts
  fireEvent.click(screen.getByRole("radio", { name: /Automations/ }));
  expect(within(table).queryByRole("link", { name: "Sync nightly" })).not.toBeInTheDocument();
  expect(within(table).getByRole("link", { name: "Mornings" })).toBeInTheDocument();
});

test("switches one off, runs one now and changes when one runs, from the list", async () => {
  wrap(<SchedulesPage />);
  await screen.findByRole("table", { name: "Schedules" });
  fireEvent.click(screen.getByRole("switch", { name: "Mornings (morning) on" }));
  await waitFor(() =>
    expect(Schedules.changeSchedule).toHaveBeenCalledWith(
      expect.objectContaining({ path: { sid: "automation:4:morning" }, body: { enabled: false } }),
    ),
  );
  fireEvent.click(screen.getByRole("button", { name: "Run Sync nightly now" }));
  await waitFor(() =>
    expect(Schedules.runSchedule).toHaveBeenCalledWith(expect.objectContaining({ path: { sid: "routine:3" } })),
  );
  expect(await screen.findByText("Sync nightly starts within half a minute.")).toBeInTheDocument();
  // change it: a custom cron, starting from the one it has, saved with Enter
  fireEvent.click(screen.getByRole("button", { name: "Change when Sync nightly runs" }));
  const dialog = await screen.findByRole("dialog");
  fireEvent.change(within(dialog).getByLabelText("Schedule"), { target: { value: "custom" } });
  const cron = within(dialog).getByLabelText("Cron");
  expect(cron).toHaveValue("0 3 * * *");
  fireEvent.change(cron, { target: { value: "0 9 * * 1-5" } });
  fireEvent.keyDown(cron, { key: "Enter" });
  await waitFor(() =>
    expect(Schedules.changeSchedule).toHaveBeenCalledWith(
      expect.objectContaining({ path: { sid: "routine:3" }, body: { cron: "0 9 * * 1-5" } }),
    ),
  );
  expect(await screen.findByText("Sync nightly: every weekday at 09:00.")).toBeInTheDocument();
});

test("switching the backup on asks for its schedule first", async () => {
  wrap(<SchedulesPage />);
  await screen.findByRole("table", { name: "Schedules" });
  fireEvent.click(screen.getByRole("switch", { name: "Back up the database on" }));
  expect(await screen.findByRole("dialog", { name: "When Back up the database runs" })).toBeInTheDocument();
  expect(Schedules.changeSchedule).not.toHaveBeenCalled();
});

test("someone who isn't an admin sees their automations' schedules and no filter", async () => {
  admin = false;
  rows = rows.filter((r) => r.kind === "automation");
  wrap(<SchedulesPage />);
  await screen.findByRole("table", { name: "Schedules" });
  expect(screen.getByText(/your automations/)).toBeInTheDocument();
  expect(screen.queryByRole("radiogroup", { name: "Show" })).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "New routine" })).not.toBeInTheDocument();
});
