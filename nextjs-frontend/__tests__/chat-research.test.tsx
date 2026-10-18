import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Chats, Research2 } from "@/app/openapi-client";
import type { Research } from "@/app/openapi-client/types.gen";
import { Composer } from "@/components/chat/composer";
import {
  estimateLine,
  newAnswers,
  planEdits,
  ReportActions,
  reportMarkdown,
  ResearchCard,
  usd,
} from "@/components/chat/research";
import { applyEvent, newTurn } from "@/components/chat/stream";
import { hrefFor } from "@/components/notes/links";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Chats: { decideApproval: jest.fn(), chatTools: jest.fn() },
  Research2: {
    getResearch: jest.fn(),
    reviseResearch: jest.fn(),
    stopResearch: jest.fn(),
    resumeResearch: jest.fn(),
    researchNote: jest.fn(),
    researchPodcast: jest.fn(),
  },
}));
jest.mock("@/lib/api/browser", () => ({ useApiClient: () => ({}), data: (p: Promise<unknown>) => p }));
jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("@/lib/hooks/session", () => ({ useArchive: () => ({}) }));
jest.mock("@/components/import/pending", () => ({ chooseFiles: jest.fn(() => Promise.resolve([])) }));
const speak = jest.fn(() => Promise.resolve());
jest.mock("@/lib/voice", () => ({ useVoice: () => ({ speak, stop: jest.fn() }) }));

const PLAN: Research = {
  id: 4,
  chat: 9,
  approval: 31,
  status: "planned",
  stage: null,
  goal: "What did customers say about pricing?",
  plan: {
    title: "Pricing feedback",
    goal: "What did customers say about pricing?",
    sub_questions: ["Which calls mention pricing?", "What do they object to?"],
    sources: ["archive"],
    max_tool_calls: 8,
    report_outline: ["Summary", "Objections"],
  },
  questions: [{ question: "Which period?", answers: ["This quarter", "All time"] }],
  answers: {},
  estimate: { usd: 0.02, tokens: 30000, tool_calls: 8, basis: "about 30,000 tokens", estimate: true },
  progress: { sub_questions: 2, done: 0, sources: 0, tool_calls: 0, cost_usd: 0.001, minutes: 0 },
  findings: [],
  revisions: 0,
};

// Radix's checkbox measures itself
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver;

const ok = (v: unknown) => Promise.resolve(v);
let qc: QueryClient;

function show(ui: React.ReactNode) {
  qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>{ui}</TooltipProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  (Chats.chatTools as jest.Mock).mockImplementation(() =>
    ok({
      groups: [
        { name: "archive", available: true },
        { name: "web", available: true },
        { name: "code", available: false },
      ],
    }),
  );
  (Chats.decideApproval as jest.Mock).mockImplementation(() => ok({ status: "done", result: { status: "queued" } }));
  (Research2.getResearch as jest.Mock).mockImplementation(() => ok(PLAN));
});
afterEach(() => qc?.clear());

describe("research helpers", () => {
  it("reads the research event into the turn", () => {
    const s = applyEvent(newTurn("/research pricing"), { event: "research", data: JSON.stringify(PLAN) });
    expect(s.research).toMatchObject({ id: 4, status: "planned" });
    expect(applyEvent(newTurn("x"), { event: "research", data: "{}" }).research).toBeNull();
  });

  it("formats small costs and scales the estimate with the tool calls, labelled", () => {
    expect(usd(0.0042)).toBe("$0.0042");
    expect(usd(2.5)).toBe("$2.50");
    expect(usd(null)).toBeNull();
    expect(estimateLine(PLAN, 16)).toBe("about $0.04 (an estimate)");
    expect(estimateLine({ ...PLAN, estimate: { usd: null, tool_calls: 8 } }, 8)).toMatch(/no estimate/);
  });

  it("sends only what changed, and only answers not sent yet", () => {
    const d = { subs: [...PLAN.plan.sub_questions], sources: PLAN.plan.sources, calls: 8, answers: {} };
    expect(planEdits(PLAN, d)).toBeNull();
    expect(planEdits(PLAN, { ...d, subs: ["Which calls mention pricing?", " "], calls: 12 })).toEqual({
      sub_questions: ["Which calls mention pricing?"],
      max_tool_calls: 12,
    });
    expect(
      newAnswers(
        { ...PLAN, answers: { "Which period?": "All time" } },
        { ...d, answers: { "Which period?": "All time", x: " " } },
      ),
    ).toEqual({});
    expect(newAnswers(PLAN, { ...d, answers: { "Which period?": "This quarter" } })).toEqual({
      "Which period?": "This quarter",
    });
  });

  it("exports the report with the sources it cites", () => {
    const md = reportMarkdown("Pricing", "Too dear [1]. See the post [3].", [
      { n: 1, text: "", title: "Renewal call", time: "4:05", speaker: "Ada", recording_id: 2 },
      { n: 2, text: "", title: "Unused" },
      { n: 3, text: "", title: "Blog", url: "https://example.com/p", source: "web" },
    ]);
    expect(md).toBe(
      "# Pricing\n\nToo dear [1]. See the post [3].\n\n## Sources\n\n- [1] Renewal call, at 4:05, Ada\n- [3] Blog <https://example.com/p>\n",
    );
  });

  it("opens a note's link back to its conversation", () => {
    expect(hrefFor("chat:12")).toBe("/chat/12");
  });
});

describe("the plan card", () => {
  it("approves the plan with Enter, sending the edits", async () => {
    show(<ResearchCard id={4} chatId={9} />);
    const first = await screen.findByRole("textbox", { name: "Sub-question 1" });
    expect(screen.getByText("Pricing feedback")).toBeInTheDocument();
    expect(screen.getByText("about $0.02 (an estimate)")).toBeInTheDocument();
    expect(await screen.findByRole("checkbox", { name: "The web" })).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: "The code sandbox" })).not.toBeInTheDocument();
    fireEvent.change(first, { target: { value: "Which calls mention price?" } });
    fireEvent.click(screen.getByRole("button", { name: "More tool calls" }));
    fireEvent.submit(first.closest("form")!);
    await waitFor(() => expect(Chats.decideApproval).toHaveBeenCalled());
    expect((Chats.decideApproval as jest.Mock).mock.calls[0][0]).toMatchObject({
      path: { aid: 31 },
      body: {
        decision: "approve",
        params: { sub_questions: ["Which calls mention price?", "What do they object to?"], max_tool_calls: 9 },
      },
    });
  });

  it("revises with the answers picked, and declines", async () => {
    (Research2.reviseResearch as jest.Mock).mockImplementation(() => ok({ ...PLAN, revisions: 1 }));
    show(<ResearchCard id={4} chatId={9} />);
    fireEvent.click(await screen.findByRole("radio", { name: "This quarter" }));
    expect(screen.getByRole("button", { name: "Revise with my answers" })).toBeInTheDocument();
    fireEvent.submit(screen.getByRole("form", { name: "Research plan" }));
    await waitFor(() => expect(Research2.reviseResearch).toHaveBeenCalled());
    expect((Research2.reviseResearch as jest.Mock).mock.calls[0][0]).toMatchObject({
      path: { rid: 4 },
      body: { answers: { "Which period?": "This quarter" } },
    });
    fireEvent.click(screen.getByRole("button", { name: "Decline" }));
    await waitFor(() =>
      expect((Chats.decideApproval as jest.Mock).mock.calls[0][0]).toMatchObject({ body: { decision: "decline" } }),
    );
  });

  it("adds and removes sub-questions, keeping one", async () => {
    show(<ResearchCard id={4} chatId={9} />);
    fireEvent.click(await screen.findByRole("button", { name: "Add a sub-question" }));
    expect(screen.getByRole("textbox", { name: "Sub-question 3" })).toHaveFocus();
    fireEvent.click(screen.getByRole("button", { name: "Remove sub-question 1" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove sub-question 1" }));
    expect(screen.getByRole("button", { name: "Remove sub-question 1" })).toBeDisabled();
  });
});

describe("the progress card", () => {
  const running: Research = {
    ...PLAN,
    status: "running",
    stage: "gather",
    findings: [{ question: "Which calls mention pricing?", tool_calls: 3 }],
    progress: {
      sub_questions: 2,
      done: 1,
      sources: 4,
      tool_calls: 3,
      cost_usd: 0.0042,
      cost_estimated: true,
      minutes: 1.5,
    },
  };

  it("shows the stage, what's done and spent, and stops the run", async () => {
    (Research2.getResearch as jest.Mock).mockImplementation(() => ok(running));
    (Research2.stopResearch as jest.Mock).mockImplementation(() => ok({ stopping: true }));
    show(<ResearchCard id={4} chatId={9} />);
    expect(await screen.findByText("Looking things up…")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "Sub-questions answered" })).toHaveAttribute("aria-valuenow", "1");
    expect(
      screen.getByText(/1 of 2 sub-questions · 4 sources · 3 of 8 tool calls · \$0.0042 \(an estimate\) · 1.5 min/),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    await waitFor(() =>
      expect(Research2.stopResearch).toHaveBeenCalledWith(expect.objectContaining({ path: { rid: 4 } })),
    );
  });

  it("says why a budget holds it, and goes on when asked", async () => {
    (Research2.getResearch as jest.Mock).mockImplementation(() =>
      ok({ ...running, status: "held", held: { why: "the monthly budget is spent", budget: "all" } }),
    );
    (Research2.resumeResearch as jest.Mock).mockImplementation(() => ok({ ...running }));
    show(<ResearchCard id={4} chatId={9} />);
    expect(await screen.findByText("Paused: the monthly budget is spent")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Go on anyway" }));
    await waitFor(() => expect(Research2.resumeResearch).toHaveBeenCalled());
  });

  it("reads the conversation again when the report is saved", async () => {
    (Research2.getResearch as jest.Mock).mockImplementation(() =>
      ok({ ...running, status: "done", stage: "done", answer: 77 }),
    );
    show(<ResearchCard id={4} chatId={9} />);
    qc.setQueryData(["chat", 9], { id: 9, messages: [{ id: 5, role: "assistant", content: "plan" }] });
    const spy = jest.spyOn(qc, "invalidateQueries");
    expect(await screen.findByText("Done: the report is below")).toBeInTheDocument();
    await waitFor(() => expect(spy).toHaveBeenCalledWith({ queryKey: ["chat", 9] }));
  });
});

describe("the report's actions", () => {
  it("saves it as a note, asking where when it could go in several namespaces", async () => {
    (Research2.researchNote as jest.Mock)
      .mockImplementationOnce(() => Promise.reject(new Error("pick a namespace: pods, sales")))
      .mockImplementationOnce(() => ok({ page: 12, namespace: "sales" }));
    show(<ReportActions id={4} text="Too dear [1]." passages={[]} />);
    fireEvent.click(screen.getByRole("button", { name: "Save as note" }));
    fireEvent.click(await screen.findByRole("button", { name: "sales" }));
    await waitFor(() =>
      expect((Research2.researchNote as jest.Mock).mock.calls[1][0]).toMatchObject({ body: { namespace: "sales" } }),
    );
    await waitFor(() => expect(screen.queryByRole("group", { name: "Save the note in" })).not.toBeInTheDocument());
  });

  it("reads it aloud, makes a podcast and exports Markdown", async () => {
    (Research2.researchPodcast as jest.Mock).mockImplementation(() =>
      ok({ episode: 5, job: 1, namespace: "podcasts" }),
    );
    const url = jest.fn(() => "blob:x");
    Object.assign(URL, { createObjectURL: url, revokeObjectURL: jest.fn() });
    show(<ReportActions id={4} text="Too dear [1]." passages={[]} />);
    fireEvent.click(screen.getByRole("button", { name: "Read aloud" }));
    expect(speak).toHaveBeenCalledWith("Too dear [1].");
    fireEvent.click(screen.getByRole("button", { name: "Make a podcast" }));
    await waitFor(() =>
      expect(Research2.researchPodcast).toHaveBeenCalledWith(expect.objectContaining({ path: { rid: 4 } })),
    );
    fireEvent.click(screen.getByRole("button", { name: "Export" }));
    expect(url).toHaveBeenCalled();
  });
});

describe("the Deep research switch", () => {
  it("turns the next message into a research goal, and says when it can't", () => {
    const onToggle = jest.fn();
    const { rerender } = render(
      <Composer
        value=""
        onChange={() => {}}
        onSend={() => {}}
        busy={false}
        placeholder="Ask"
        research={{ on: false, onToggle }}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Deep research" }));
    expect(onToggle).toHaveBeenCalled();
    // the goal is typed next, without a click into the box
    expect(screen.getByRole("textbox", { name: "Your question" })).toHaveFocus();
    rerender(
      <Composer
        value=""
        onChange={() => {}}
        onSend={() => {}}
        busy={false}
        placeholder="Ask"
        research={{ on: true, onToggle }}
      />,
    );
    expect(screen.getByRole("button", { name: "Deep research" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("Deep research: you approve a plan first")).toBeInTheDocument();
    rerender(
      <Composer
        value=""
        onChange={() => {}}
        onSend={() => {}}
        busy={false}
        placeholder="Ask"
        research={{ on: false, onToggle, off: "Deep research takes a goal, not files" }}
      />,
    );
    expect(screen.getByRole("button", { name: "Deep research" })).toBeDisabled();
  });
});
