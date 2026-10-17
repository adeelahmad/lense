import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom";
import type { ReactNode } from "react";

import { Chats } from "@/app/openapi-client";
import type { Approval } from "@/app/openapi-client/types.gen";
import { ApprovalsBadge } from "@/components/approvals/approvals-badge";
import { ApprovalsPage, UNDO_MS } from "@/components/approvals/approvals-page";
import { ToastProvider } from "@/components/ui/toast";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Chats: {
    listApprovals: jest.fn(),
    getApproval: jest.fn(),
    decideApproval: jest.fn(),
    countApprovals: jest.fn(),
  },
}));
let search = "";
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
  usePathname: () => "/approvals",
  useSearchParams: () => new URLSearchParams(search),
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const m = (f: unknown) => f as jest.Mock;
const CAN = { approve: true, decline: true, edit: true, always: true };

function approval(id: number, over: Partial<Approval> = {}): Approval {
  return {
    id,
    tool: "action",
    status: "pending",
    summary: `Post “Trip ${id}” to the family room`,
    origin: { automation: 4, run: 51, node: `n${id}` },
    automation: "School emails",
    intent: { action: "test_post", params: { text: `Trip ${id}`, times: 1 }, effect: "out" },
    why: { activity: "create-recording-7", summary: "Work Gmail received “School trip”", url: "/resources/7" },
    expires_at: new Date(Date.now() + 6.5 * 86_400_000).toISOString(),
    created_at: new Date().toISOString(),
    can: CAN,
    ...over,
  };
}

let waiting: Approval[] = [];
function serve(recent: Approval[] = []) {
  m(Chats.listApprovals).mockImplementation(({ query }: { query: { status?: string } }) =>
    ok(query.status === "pending" ? waiting : [...waiting, ...recent]),
  );
  m(Chats.decideApproval).mockImplementation(
    ({ path, body }: { path: { aid: number }; body: { decision: string } }) => {
      waiting = waiting.filter((a) => a.id !== path.aid);
      return ok(
        body.decision === "decline" ? { status: "declined" } : { status: "done", always: body.decision === "always" },
      );
    },
  );
  m(Chats.countApprovals).mockImplementation(() => ok({ waiting: waiting.length, automations: waiting.length }));
}

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
  search = "";
  waiting = [
    approval(12),
    approval(11),
    approval(9, {
      origin: null,
      intent: null,
      tool: "run_template",
      chat: 3,
      automation: null,
      summary: "Run notes",
      can: { approve: true, decline: true },
    }),
  ];
});

describe("the approvals inbox", () => {
  it("groups what waits, puts the first in focus, and Enter approves it", async () => {
    serve();
    wrap(<ApprovalsPage />);
    const first = await screen.findByRole("group", { name: "Approval: Post “Trip 12” to the family room" });
    expect(screen.getByRole("region", { name: "School emails" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "The assistant, in a conversation" })).toBeInTheDocument();
    expect(screen.getByText("3 wait for your pick")).toBeInTheDocument();
    await waitFor(() => expect(first).toHaveFocus());
    expect(within(first).getByText(/Reaches outside Lens · Expires in 6 days/)).toBeInTheDocument();
    expect(within(first).getByRole("link", { name: "Work Gmail received “School trip”" })).toHaveAttribute(
      "href",
      "/resources/7",
    );
    fireEvent.keyDown(first, { key: "Enter" });
    await waitFor(() =>
      expect(Chats.decideApproval).toHaveBeenCalledWith(
        expect.objectContaining({ path: { aid: 12 }, body: { decision: "approve" } }),
      ),
    );
    const next = await screen.findByRole("group", { name: "Approval: Post “Trip 11” to the family room" });
    await waitFor(() => expect(next).toHaveFocus()); // focus moves on to the next one
    expect(screen.queryByRole("group", { name: /Trip 12/ })).not.toBeInTheDocument();
  });

  it("declines with Undo instead of a question, and sends it once the notice is gone", async () => {
    jest.useFakeTimers();
    try {
      serve();
      wrap(<ApprovalsPage />);
      const card = await screen.findByRole("group", { name: /Trip 12/ });
      fireEvent.click(within(card).getByRole("button", { name: "Decline" }));
      expect(screen.queryByRole("group", { name: /Trip 12/ })).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Undo" }));
      expect(await screen.findByRole("group", { name: /Trip 12/ })).toBeInTheDocument();
      act(() => jest.advanceTimersByTime(UNDO_MS + 100));
      expect(Chats.decideApproval).not.toHaveBeenCalled(); // taken back: nothing sent
      fireEvent.click(within(screen.getByRole("group", { name: /Trip 12/ })).getByRole("button", { name: "Decline" }));
      act(() => jest.advanceTimersByTime(UNDO_MS + 100));
      await waitFor(() =>
        expect(Chats.decideApproval).toHaveBeenCalledWith(
          expect.objectContaining({ path: { aid: 12 }, body: { decision: "decline" } }),
        ),
      );
    } finally {
      jest.useRealTimers();
    }
  });

  it("edits the params, typed as they were, and Always allow is there only when it can be", async () => {
    waiting = [approval(12), approval(11, { can: { ...CAN, always: false } })];
    serve();
    wrap(<ApprovalsPage />);
    const card = await screen.findByRole("group", { name: /Trip 12/ });
    expect(within(card).getByRole("button", { name: "Always allow" })).toBeInTheDocument();
    expect(
      within(screen.getByRole("group", { name: /Trip 11/ })).queryByRole("button", { name: "Always allow" }),
    ).not.toBeInTheDocument();
    fireEvent.click(within(card).getByRole("button", { name: "Edit" }));
    const text = within(card).getByLabelText("text");
    expect(text).toHaveFocus();
    fireEvent.change(text, { target: { value: "Trip on Friday" } });
    fireEvent.change(within(card).getByLabelText("times"), { target: { value: "2" } });
    fireEvent.submit(within(card).getByRole("form", { name: "Change what it does" }));
    await waitFor(() =>
      expect(Chats.decideApproval).toHaveBeenCalledWith(
        expect.objectContaining({
          path: { aid: 12 },
          body: { decision: "edit", params: { text: "Trip on Friday", times: 2 } },
        }),
      ),
    );
  });

  it("brings the one you were sent first, and says what became of one already decided", async () => {
    search = "id=11";
    serve();
    const { unmount } = wrap(<ApprovalsPage />);
    const cards = await screen.findAllByRole("group", { name: /^Approval: Post/ });
    expect(cards[0]).toHaveAccessibleName("Approval: Post “Trip 11” to the family room");
    unmount();
    search = "id=5";
    m(Chats.getApproval).mockImplementation(() =>
      ok(approval(5, { status: "declined", decided_by: "sam@x.io", summary: "Post “Old”" })),
    );
    wrap(<ApprovalsPage />);
    expect(await screen.findByText("Declined by sam@x.io: Post “Old”")).toBeInTheDocument();
  });

  it("an approval you can't approve from here says why", async () => {
    waiting = [approval(12, { can: { approve: false, decline: true, edit: false, always: false } })];
    serve();
    wrap(<ApprovalsPage />);
    const card = await screen.findByRole("group", { name: /Trip 12/ });
    expect(within(card).getByRole("button", { name: "Approve" })).toHaveAttribute("aria-disabled", "true");
    fireEvent.keyDown(card, { key: "Enter" });
    expect(Chats.decideApproval).not.toHaveBeenCalled();
    expect(within(card).queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
  });

  it("says when nothing waits", async () => {
    waiting = [];
    serve();
    wrap(<ApprovalsPage />);
    expect(await screen.findByText("Nothing waits for your pick")).toBeInTheDocument();
  });
});

describe("the top bar's approvals badge", () => {
  it("shows how many wait and opens the inbox, and nothing when none do", async () => {
    serve();
    const { unmount } = wrap(<ApprovalsBadge />);
    const link = await screen.findByRole("link", { name: "3 waiting for your approval" });
    expect(link).toHaveAttribute("href", "/approvals");
    expect(link).toHaveTextContent("3");
    unmount();
    waiting = [];
    wrap(<ApprovalsBadge />);
    await waitFor(() => expect(Chats.countApprovals).toHaveBeenCalledTimes(2));
    await act(async () => undefined);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
});
