import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";
import type { ReactNode } from "react";

import { Dates } from "@/app/openapi-client";
import type { Approval, DateSaid } from "@/app/openapi-client/types.gen";
import { groupApprovals } from "@/components/approvals/model";
import { dayWords, SaidDates } from "@/components/recording/said-dates";
import { ToastProvider } from "@/components/ui/toast";

jest.mock("@/app/openapi-client", () => ({ Dates: { listDates: jest.fn(), proposeEvent: jest.fn() } }));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
const seek = jest.fn();
jest.mock("@/components/player/media", () => ({ usePlayerApi: () => ({ seek }) }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const m = (f: unknown) => f as jest.Mock;

function said(over: Partial<DateSaid>): DateSaid {
  return {
    day: "2026-10-20",
    until: "2026-10-20",
    period: "day",
    said: "next Tuesday",
    relative: true,
    recording_id: 4,
    segment: 31,
    idx: 1,
    t0: 12_000,
    text: "Let's meet next Tuesday at 3.",
    ...over,
  };
}

function wrap(children: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ToastProvider>{children}</ToastProvider>
    </QueryClientProvider>,
  );
}

describe("dates said in a recording", () => {
  beforeEach(() => jest.clearAllMocks());

  it("proposes an event from a date in one click, and says it waits in Approvals", async () => {
    const weekend = said({
      day: "2026-10-24",
      until: "2026-10-25",
      period: "weekend",
      said: "next weekend",
      segment: 32,
    });
    m(Dates.listDates).mockReturnValue(ok({ total: 2, items: [said({}), weekend], reading: true, proposable: true }));
    m(Dates.proposeEvent).mockReturnValue(ok({ approval: 77, status: "waiting", summary: "Add …", again: false }));
    wrap(<SaidDates id={4} />);
    expect(await screen.findByText("Tue 20 Oct 2026")).toBeInTheDocument();
    expect(screen.getByText("24 Oct – 25 Oct 2026")).toBeInTheDocument();
    fireEvent.click(screen.getByText("“next Tuesday”"));
    expect(seek).toHaveBeenCalledWith(12_000, { manual: true });

    fireEvent.click(screen.getByRole("button", { name: "Propose an event on Tue 20 Oct 2026" }));
    await waitFor(() =>
      expect(Dates.proposeEvent).toHaveBeenCalledWith(
        expect.objectContaining({ body: { recording_id: 4, segment: 31, index: 0 } }),
      ),
    );
    expect(await screen.findByRole("link", { name: "Waiting in Approvals" })).toHaveAttribute(
      "href",
      "/approvals?id=77",
    );
    expect(screen.getByText("Nothing is added to the calendar until you approve it.")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /^Propose an event/ })).toHaveLength(1); // the weekend's is still there
  });

  it("offers no Propose event without a calendar that adds events, and shows nothing without dates", async () => {
    m(Dates.listDates).mockReturnValue(ok({ total: 1, items: [said({})], reading: true, proposable: false }));
    const { unmount } = wrap(<SaidDates id={4} />);
    expect(await screen.findByText("Tue 20 Oct 2026")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Propose/ })).not.toBeInTheDocument();
    unmount();
    m(Dates.listDates).mockReturnValue(ok({ total: 0, items: [], reading: true, proposable: true }));
    wrap(<SaidDates id={4} />);
    await waitFor(() => expect(Dates.listDates).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole("region", { name: "Dates said" })).not.toBeInTheDocument();
  });

  it("reads days in words and groups proposals apart from conversations", () => {
    expect(dayWords({ day: "2026-11-01", until: "2026-11-30", period: "month" })).toBe("November 2026");
    const a = { id: 1, tool: "add_event", status: "pending", chat: null } as Approval;
    const b = { id: 2, tool: "add_event", status: "pending", chat: 5 } as Approval;
    expect(groupApprovals([a, b]).map((g) => g.title)).toEqual([
      "Events proposed from dates said",
      "The assistant, in a conversation",
    ]);
  });
});
