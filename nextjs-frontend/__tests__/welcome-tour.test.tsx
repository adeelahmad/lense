import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { tourDue, WelcomeTour } from "@/components/home/welcome-tour";

const push = jest.fn();
const finishTour = jest.fn();
let me: Record<string, unknown> | undefined;
jest.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
jest.mock("@/lib/hooks/session", () => ({ useArchive: () => ({ me }) }));
jest.mock("@/lib/api/browser", () => ({ useApiClient: () => ({}), data: (p: Promise<unknown>) => p }));
jest.mock("@/app/openapi-client", () => ({ Auth: { finishTour: (...a: unknown[]) => finishTour(...a) } }));

function show() {
  const qc = new QueryClient();
  render(
    <QueryClientProvider client={qc}>
      <WelcomeTour />
    </QueryClientProvider>,
  );
  return qc;
}

beforeEach(() => {
  push.mockReset();
  finishTour.mockReset().mockResolvedValue({ via: "access", toured_at: "2026-10-05T00:00:00+00:00" });
  me = { via: "access", toured_at: null, user: {} };
});

describe("welcome tour", () => {
  it("is due once, for someone signed in to the web app", () => {
    expect(tourDue({ via: "access", toured_at: null })).toBe(true);
    expect(tourDue({ via: "access", toured_at: "2026-10-05" })).toBe(false);
    expect(tourDue({ via: "token", toured_at: null })).toBe(false);
    expect(tourDue(undefined)).toBe(false);
  });

  it("asks the assistant in one step and counts as seen", async () => {
    const qc = show();
    fireEvent.change(screen.getByLabelText("Ask the assistant"), { target: { value: "what's new?" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    expect(push).toHaveBeenCalledWith("/chat?global=1&q=what%27s+new%3F");
    await waitFor(() => expect(finishTour).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(qc.getQueryData(["me"])).toMatchObject({ toured_at: expect.any(String) }));
  });

  it("opens Reports, or skips, and closes", async () => {
    show();
    fireEvent.click(screen.getByRole("button", { name: "Open Reports" }));
    expect(push).toHaveBeenCalledWith("/reports");
    expect(screen.queryByText("Welcome to Lens")).not.toBeInTheDocument();
    await waitFor(() => expect(finishTour).toHaveBeenCalledTimes(1));
  });

  it("stays shut once seen, and the account menu opens it again without recording anything", async () => {
    me = { via: "access", toured_at: "2026-10-05T00:00:00+00:00", user: {} };
    show();
    expect(screen.queryByText("Welcome to Lens")).not.toBeInTheDocument();
    act(() => {
      window.dispatchEvent(new CustomEvent("lens:tour"));
    });
    expect(screen.getByText("Welcome to Lens")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Skip" }));
    expect(screen.queryByText("Welcome to Lens")).not.toBeInTheDocument();
    expect(finishTour).not.toHaveBeenCalled();
  });
});
