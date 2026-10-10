import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";

import { BudgetMeter } from "@/components/costs/budget-meter";

const budgetMeter = jest.fn();
let archive = { namespace: "pods" as string | null, admin: false, isPartial: () => false };
jest.mock("@/app/openapi-client", () => ({ Budgets: { budgetMeter: (o: unknown) => budgetMeter(o) } }));
jest.mock("@/lib/api/browser", () => ({ useApiClient: () => ({}), data: (p: Promise<unknown>) => p }));
jest.mock("@/lib/hooks/session", () => ({ useArchive: () => archive }));

function show() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <BudgetMeter />
    </QueryClientProvider>,
  );
}

const status = (share: number, state: string) => ({
  resource: "space:1",
  state,
  share,
  spent: { usd: share * 5, tokens: 100, estimate: false },
  left: { usd: Math.max(0, 5 - share * 5) },
  budget: { resource: "space:1", usd: 5, period: "week", on_over: "ask", warn_at: 0.8 },
  next: { runs: 0, basis: "" },
});

afterEach(() => budgetMeter.mockReset());

describe("the top bar's budget meter", () => {
  it("shows used and left against the namespace's budget, highlighted when near", async () => {
    budgetMeter.mockResolvedValue({
      resource: "space:1",
      namespace: "pods",
      period: "week",
      spent: { usd: 4.5, tokens: 100, estimate: false },
      calls: 3,
      budget: status(0.9, "near"),
    });
    show();
    const btn = await screen.findByRole("button", { name: /Spent this week in pods, \$0\.50 left/ });
    expect(btn).toHaveTextContent("$4.50 / $5.00");
    expect(btn.className).toContain("bg-gold-surface");
    expect(budgetMeter).toHaveBeenCalledWith(
      expect.objectContaining({ query: expect.objectContaining({ ns: "pods" }) }),
    );
  });

  it("shows what was spent when there's no budget, and nothing for a member on all namespaces", async () => {
    budgetMeter.mockResolvedValue({
      resource: "space:1",
      period: "month",
      spent: { usd: 1.25, tokens: 100, estimate: false },
      calls: 3,
      budget: null,
    });
    const { unmount } = show();
    expect(await screen.findByRole("button", { name: /Spent this month in pods\. Details/ })).toHaveTextContent(
      "$1.25",
    );
    unmount();

    archive = { namespace: null, admin: false, isPartial: () => false };
    budgetMeter.mockClear();
    const { container } = show();
    expect(container).toBeEmptyDOMElement();
    expect(budgetMeter).not.toHaveBeenCalled();
  });
});
