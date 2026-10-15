import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";
import type { ReactNode } from "react";

import { Admin } from "@/app/openapi-client";
import { AnalyticsPage } from "@/components/analytics/analytics-page";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Admin: { usageMetrics: jest.fn(), usageEvents: jest.fn(), analyticsStatus: jest.fn() },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
const archive = { admin: true };
jest.mock("@/lib/hooks/session", () => ({ useArchive: () => archive }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const m = (f: unknown) => f as jest.Mock;

const METRICS = (days: number) => ({
  days,
  since: "2026-10-04",
  until: "2026-10-10",
  daily: [
    { day: "2026-10-09", active_users: 1, actions: 3 },
    { day: "2026-10-10", active_users: 2, actions: 2 },
  ],
  dau: 2,
  wau: 2,
  mau: 3,
  stickiness: 0.1429,
  accounts: 3,
  active_users: 2,
  new_users: 1,
  churn: { previous_active: 2, retained: 1, churned: 1, rate: 0.5, returning: 1 },
  retention: [{ week: "2026-10-05", users: 1, retention: [1] }],
  features: [{ feature: "chats", actions: 2, users: 1 }],
  last_active_day: "2026-10-10",
  days_since_active: 0,
});

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>{ui}</TooltipProvider>
    </QueryClientProvider>,
  );
}

describe("AnalyticsPage", () => {
  beforeEach(() => {
    archive.admin = true;
    m(Admin.usageMetrics).mockImplementation(({ query }: { query: { days: number } }) => ok(METRICS(query.days)));
    m(Admin.usageEvents).mockImplementation(() =>
      ok([
        {
          at: "2026-10-10T00:05:00+00:00",
          day: "2026-10-09",
          name: "lens_daily_active",
          params: { active_users: 1 },
          sent: null,
        },
      ]),
    );
    m(Admin.analyticsStatus).mockImplementation(() => ok({ enabled: false, configured: false, events: [] }));
  });

  it("shows churn, retention and the kept events with Google Analytics off", async () => {
    wrap(<AnalyticsPage />);
    expect(await screen.findByText("50%")).toBeInTheDocument(); // churn rate
    expect(screen.getByText("1 of 2 didn’t come back")).toBeInTheDocument();
    expect(screen.getByText("Retention by sign-up week")).toBeInTheDocument();
    expect(await screen.findByText("Daily active")).toBeInTheDocument();
    expect(screen.getByText("kept here only")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Settings → Usage analytics" })).toHaveAttribute(
      "href",
      "/settings/analytics",
    );
    fireEvent.click(screen.getByRole("radio", { name: "90 days" }));
    await waitFor(() =>
      expect(Admin.usageMetrics).toHaveBeenCalledWith(expect.objectContaining({ query: { days: 90 } })),
    );
  });

  it("is for admins", () => {
    archive.admin = false;
    wrap(<AnalyticsPage />);
    expect(screen.getByText("Analytics are for admins")).toBeInTheDocument();
    expect(Admin.usageMetrics).not.toHaveBeenCalled();
  });
});
