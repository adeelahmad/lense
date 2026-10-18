import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom";
import type { ReactNode } from "react";

import { Activities, Automations } from "@/app/openapi-client";
import type { Automation } from "@/app/openapi-client/types.gen";
import { AutomationBuilder, problemAt } from "@/components/automations/automation-builder";
import { AutomationsPage } from "@/components/automations/automations-page";
import { ToastProvider } from "@/components/ui/toast";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Automations: {
    listAutomations: jest.fn(),
    listActions: jest.fn(),
    createAutomation: jest.fn(),
    updateAutomation: jest.fn(),
    enableAutomation: jest.fn(),
    deleteAutomation: jest.fn(),
    restoreAutomation: jest.fn(),
    runAutomation: jest.fn(),
    variableCatalog: jest.fn(),
    testDraft: jest.fn(),
    draftAutomation: jest.fn(),
    setPolicy: jest.fn(),
  },
  Activities: { listActivityKinds: jest.fn(), listActivities: jest.fn() },
  Activity: { resourceCosts: jest.fn(() => Promise.resolve({ data: { costs: {} }, response: { ok: true } })) },
  Budgets: { listBudgets: jest.fn(() => Promise.resolve({ data: [], response: { ok: true } })) },
  Routines: { previewSchedule: jest.fn() },
  Pipelines: {},
  Workflows: {},
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
const push = jest.fn();
jest.mock("next/navigation", () => ({ useRouter: () => ({ push, replace: jest.fn() }) }));
jest.mock("@/lib/hooks/session", () => ({
  useArchive: () => ({
    admin: true,
    me: { user: { admin: true } },
    namespaces: [{ id: 3, name: "home" }],
    can: () => true,
  }),
}));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const m = (f: unknown) => f as jest.Mock;

const ACTIONS = [
  {
    name: "notify",
    label: "Send a message",
    group: "Messages",
    description: "Tells you in your own chat app",
    params: {
      type: "object",
      properties: { to: { type: "string", title: "To", default: "me" }, text: { type: "string", title: "Message" } },
    },
    required: ["text"],
    effect: "depends",
  },
  {
    name: "http_request",
    label: "Call a web address",
    group: "Web",
    params: { type: "object", properties: { url: { type: "string", title: "Address" } } },
    required: ["url"],
    effect: "out",
  },
];
const KINDS = [
  {
    name: "email.received",
    label: "Email received",
    source: "Email",
    type: "Create",
    object_type: "Note",
    schema: {
      type: "object",
      properties: {
        summary: { type: "string", title: "Summary" },
        object: { type: "object", title: "What", properties: { subject: { type: "string", title: "Subject" } } },
      },
    },
  },
];
const ROW = {
  id: 7,
  name: "School emails",
  enabled: true,
  run_as: 1,
  triggers: [{ as: "email", on: "event", kinds: ["email.received"] }],
  join: { mode: "any" },
  workflow: 12,
  graph: {
    nodes: [
      { id: "in", type: "input", config: {} },
      { id: "notify", type: "action", config: { action: "notify", params: { text: "{{ event.summary }}" } } },
    ],
    edges: [{ source: "in", target: "notify" }],
  },
  policy: {},
  limits: {},
  counters: {},
  last_status: "done",
  last_run_at: "2026-10-18T06:00:00Z",
  waiting: 2,
  runs_today: 3,
  reads: "“Email received”, where the subject contains “trip”",
  next_at: null,
} as unknown as Automation;

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

beforeEach(() => {
  m(Automations.listActions).mockReturnValue(ok(ACTIONS));
  m(Activities.listActivityKinds).mockReturnValue(ok(KINDS));
  m(Automations.variableCatalog).mockReturnValue(
    ok({
      variables: [
        { path: "event.summary", type: "string", label: "Summary", group: "The activity" },
        { path: "events.email.object.subject", type: "string", label: "Subject", group: "email" },
      ],
    }),
  );
  m(Activities.listActivities).mockReturnValue(
    ok({
      items: [{ id: "act-1", kind: "email.received", summary: "Trip on Friday", published: "2026-10-18T05:00:00Z" }],
    }),
  );
});

test("the list reads each automation, and Delete is undone from its toast", async () => {
  m(Automations.listAutomations).mockReturnValue(ok({ automations: [ROW] }));
  m(Automations.deleteAutomation).mockReturnValue(ok({ ok: true }));
  m(Automations.restoreAutomation).mockReturnValue(ok({ ok: true }));
  wrap(<AutomationsPage />);
  const row = (await screen.findByRole("link", { name: "School emails" })).closest("tr")!;
  expect(within(row).getByText("“Email received”, where the subject contains “trip”")).toBeInTheDocument();
  expect(within(row).getByText("Done")).toBeInTheDocument();
  expect(within(row).getByText("3")).toBeInTheDocument();
  expect(within(row).getByRole("link", { name: "2" })).toHaveAttribute("href", "/approvals");
  expect(within(row).getByRole("switch", { name: "School emails on" })).toBeChecked();
  fireEvent.keyDown(within(row).getByRole("button", { name: "More for School emails" }), { key: "Enter" });
  fireEvent.click(await screen.findByRole("menuitem", { name: "Delete" }));
  await waitFor(() => expect(Automations.deleteAutomation).toHaveBeenCalled());
  fireEvent.click(await screen.findByRole("button", { name: "Undo" }));
  await waitFor(() => expect(Automations.restoreAutomation).toHaveBeenCalled());
});

test("an empty list says what automations are for", async () => {
  m(Automations.listAutomations).mockReturnValue(ok({ automations: [] }));
  wrap(<AutomationsPage />);
  expect(await screen.findByText("No automations yet")).toBeInTheDocument();
});

test("the builder adds a trigger with a condition and an action, then saves it switched off", async () => {
  m(Automations.createAutomation).mockReturnValue(ok({ id: 9, hooks: [] }));
  const saved = jest.fn();
  wrap(<AutomationBuilder onSaved={saved} />);
  expect(screen.getByLabelText("Describe it in words")).toHaveFocus();
  // When: an email arrives, only when the subject contains trip
  fireEvent.keyDown(screen.getByRole("button", { name: "Add a trigger" }), { key: "Enter" });
  fireEvent.click(await screen.findByRole("menuitem", { name: "Email received" }));
  expect(await screen.findByRole("heading", { name: "Email received" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Add an only-when condition" }));
  fireEvent.change(screen.getByLabelText("Field 1"), { target: { value: "object.subject" } });
  fireEvent.change(screen.getByLabelText("Value 1"), { target: { value: "trip" } });
  // Then: send a message, its text from the variable picker
  fireEvent.keyDown(screen.getByRole("button", { name: "Add an action" }), { key: "Enter" });
  fireEvent.click(await screen.findByRole("menuitem", { name: "Send a message" }));
  expect(await screen.findByText("Asks you first when it reaches other people")).toBeInTheDocument();
  const text = screen.getByLabelText(/^Message/);
  await waitFor(() => expect(Automations.variableCatalog).toHaveBeenCalled());
  fireEvent.change(text, { target: { value: "School: {{ eve", selectionStart: 14 } });
  fireEvent.keyDown(text, { key: "Enter" });
  expect(text).toHaveValue("School: {{ event.summary }}");
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(Automations.createAutomation).toHaveBeenCalled());
  const body = m(Automations.createAutomation).mock.calls[0][0].body;
  expect(body).toMatchObject({
    name: "Send a message on email received",
    enabled: false,
    triggers: [
      {
        as: "email",
        on: "event",
        kinds: ["email.received"],
        where: { all: [{ path: "object.subject", op: "contains", value: "trip" }] },
      },
    ],
    if: null,
  });
  expect(body.graph.nodes[1]).toMatchObject({
    type: "action",
    config: { action: "notify", params: { text: "School: {{ event.summary }}" } },
  });
  expect(saved).toHaveBeenCalledWith(9, []);
});

test("Describe it in words fills the builder and says what it left out", async () => {
  m(Automations.draftAutomation).mockReturnValue(
    ok({
      draft: {
        name: "Tell me about school emails",
        triggers: [{ as: "mail", on: "event", kinds: ["email.received"] }],
        graph: ROW.graph,
      },
      dropped: [],
      unmapped: ["when both arrive"],
    }),
  );
  wrap(<AutomationBuilder />);
  const box = screen.getByLabelText("Describe it in words");
  fireEvent.change(box, { target: { value: "When the school emails, tell me" } });
  fireEvent.keyDown(box, { key: "Enter" });
  expect(await screen.findByDisplayValue("Tell me about school emails")).toBeInTheDocument();
  expect(screen.getByText("Not done: when both arrive")).toBeInTheDocument();
  expect(Automations.createAutomation).not.toHaveBeenCalled();
});

test("the test panel runs the draft on a recent activity and says what it would do", async () => {
  m(Automations.testDraft).mockReturnValue(
    ok({
      status: "done",
      trace: { notify: { status: "done" } },
      log: ["would tell you"],
      intents: [{ node: "notify", action: "notify", status: "done", risk: "routine" }],
      steps: 1,
    }),
  );
  wrap(<AutomationBuilder automation={ROW} />);
  fireEvent.click(await screen.findByRole("button", { name: "Test" }));
  const panel = screen.getByRole("complementary", { name: "Test" });
  await within(panel).findByRole("option", { name: /Trip on Friday/ });
  fireEvent.click(within(panel).getByRole("button", { name: "Test" }));
  expect(await within(panel).findByText("notify · Send a message: would run at once")).toBeInTheDocument();
  expect(m(Automations.testDraft).mock.calls[0][0].body).toMatchObject({
    activity: "act-1",
    draft: { name: "School emails" },
  });
});

test("a saved automation lets an outside action run on its own, and a branching one opens in the canvas", async () => {
  const web = {
    ...ROW,
    graph: {
      nodes: [
        { id: "in", type: "input", config: {} },
        { id: "http", type: "action", config: { action: "http_request", params: { url: "https://example.org" } } },
      ],
      edges: [{ source: "in", target: "http" }],
    },
  } as unknown as Automation;
  m(Automations.setPolicy).mockReturnValue(ok({ http: "auto" }));
  const { unmount } = wrap(<AutomationBuilder automation={web} />);
  fireEvent.click(await screen.findByRole("switch", { name: "Let Call a web address run on its own" }));
  await waitFor(() =>
    expect(m(Automations.setPolicy).mock.calls[0][0]).toMatchObject({
      path: { aid: 7 },
      body: { node: "http", value: "auto" },
    }),
  );
  expect(await screen.findByText("Runs on its own")).toBeInTheDocument();
  unmount();
  const branching = {
    ...ROW,
    graph: {
      ...ROW.graph,
      nodes: [...(ROW.graph as { nodes: unknown[] }).nodes, { id: "d", type: "decide", config: {} }],
    },
  } as unknown as Automation;
  wrap(<AutomationBuilder automation={branching} />);
  expect(await screen.findByText("This automation branches; edit it in the canvas.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Open in canvas" })).toHaveAttribute("href", "/workflows/12");
});

test("API problems go next to what they are about", () => {
  const d = {
    name: "",
    triggers: [{ as: "mail", on: "event" }],
    join: { mode: "any" as const },
    if: null,
    steps: [{ id: "a", action: "notify", params: {} }],
    graph: { nodes: [], edges: [] },
  };
  expect(problemAt("trigger mail: choose kinds", d)).toBe("trigger.0");
  expect(problemAt("action node a reaches outside Lens", d)).toBe("step.a");
  expect(problemAt("limits: cooldown is a duration", d)).toBe("");
});
