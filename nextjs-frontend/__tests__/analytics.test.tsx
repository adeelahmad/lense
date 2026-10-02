import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom";

import type { ActivityEntry, AnalyticsReport, DayCounts } from "@/app/openapi-client/types.gen";
import { ActivityPage } from "@/components/account/activity";
import { AnalyticsScreen } from "@/components/analytics/analytics-screen";
import { DayBars } from "@/components/analytics/day-bars";
import { activityLine, dayLabel, rangeDays, tickDays, totalOf, whyNot } from "@/components/analytics/model";
import { ReportPlay } from "@/components/analytics/play-report";
import { TooltipProvider } from "@/components/ui/tooltip";

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const fail = (status: number, detail: string) =>
  Promise.resolve({ error: { detail }, response: { ok: false, status } });
const api = {
  getAnalytics: jest.fn<Promise<unknown>, [{ query: Record<string, unknown> }]>(),
  myActivity: jest.fn<Promise<unknown>, [{ query: Record<string, unknown> }]>(),
  played: jest.fn<Promise<unknown>, [unknown]>(),
  playedPublic: jest.fn<Promise<unknown>, [unknown]>(),
  collections: jest.fn<Promise<unknown>, [unknown]>(),
};
jest.mock("@/app/openapi-client", () => ({
  Analytics: {
    getAnalytics: (a: { query: Record<string, unknown> }) => api.getAnalytics(a),
    myActivity: (a: { query: Record<string, unknown> }) => api.myActivity(a),
    played: (a: unknown) => api.played(a),
    playedPublic: (a: unknown) => api.playedPublic(a),
  },
  Namespaces: { listNamespaceCollections: (a: unknown) => api.collections(a) },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
let archive: {
  namespace: string | null;
  admin: boolean;
  loaded: boolean;
  can: () => boolean;
  isPartial: () => boolean;
} = { namespace: "podcasts", admin: false, loaded: true, can: () => true, isPartial: () => false };
jest.mock("@/lib/hooks/session", () => ({ useArchive: () => archive }));
let playing = false;
jest.mock("@/components/player/media", () => ({ usePlayerState: () => ({ playing }) }));

const wrap = (ui: React.ReactNode) =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <TooltipProvider delayDuration={0}>{ui}</TooltipProvider>
    </QueryClientProvider>,
  );
const zero = { view: 0, play: 0, search: 0, download: 0, comment: 0 };
const days: DayCounts[] = [
  { day: "2026-10-01", ...zero },
  { day: "2026-10-02", ...zero, view: 5, play: 2 },
  { day: "2026-10-03", ...zero, view: 1, search: 4 },
];
const report = (over: Partial<AnalyticsReport> = {}): AnalyticsReport => ({
  from: "2026-10-01",
  to: "2026-10-03",
  namespace: "podcasts",
  collection: null,
  totals: { view: 6, play: 2, search: 4, download: 0, comment: 0 },
  people: 3,
  anonymous: 2,
  days,
  collections: [{ id: 1, name: "Season 2", path: ["General", "Season 2"], ...zero, view: 6, play: 2 }],
  namespaces: null,
  resources: [{ id: 7, title: "At sea", namespace: "podcasts", ...zero, view: 6, play: 2 }],
  ...over,
});

describe("analytics model", () => {
  it("adds up, labels days and picks the ticks", () => {
    expect(totalOf({ view: 2, play: 1 })).toBe(3);
    expect(dayLabel("2026-10-03")).toBe("3 Oct");
    expect(dayLabel("soon")).toBe("soon");
    expect(rangeDays("7")).toBe(7);
    expect(rangeDays("12")).toBe(30);
    expect([...tickDays(days)]).toEqual(["2026-10-01", "2026-10-02", "2026-10-03"]);
    const month = Array.from({ length: 30 }, (_, i) => ({ day: `d${i}` }));
    const ticks = tickDays(month);
    expect(ticks.size).toBe(6);
    expect(ticks.has("d0") && ticks.has("d29")).toBe(true);
  });

  it("says what someone did", () => {
    const e = (over: Partial<ActivityEntry>): ActivityEntry => ({
      at: "2026-10-03T10:00:00+00:00",
      action: "view",
      ...over,
    });
    expect(activityLine(e({ resource: 7, title: "At sea" }))).toEqual({ did: "Opened", what: "At sea" });
    expect(activityLine(e({ action: "play", resource: 7, title: null }))).toEqual({
      did: "Played",
      what: "a resource you can no longer open",
    });
    expect(activityLine(e({ action: "search", namespace: "podcasts" }))).toEqual({ did: "Searched", what: "podcasts" });
    expect(activityLine(e({ action: "search" }))).toEqual({ did: "Searched", what: null });
    expect(activityLine(e({ action: "comment", resource: null }))).toEqual({ did: "Commented on", what: null });
  });

  it("says who may see a namespace's analytics", () => {
    expect(whyNot({ namespace: null, admin: true, owner: false, partial: false })).toBeNull();
    expect(whyNot({ namespace: "podcasts", admin: false, owner: true, partial: false })).toBeNull();
    expect(whyNot({ namespace: null, admin: false, owner: false, partial: false })).toMatch(/Pick a namespace you own/);
    expect(whyNot({ namespace: "podcasts", admin: false, owner: false, partial: false })).toBe(
      "Owners of podcasts can see its analytics.",
    );
    expect(whyNot({ namespace: "podcasts", admin: false, owner: false, partial: true })).toMatch(/Pick a collection/);
  });
});

describe("the charts of days", () => {
  it("draws one small chart per action and offers the numbers as a table", () => {
    wrap(<DayBars days={days} />);
    expect(screen.getByRole("group", { name: "Views per day" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Searches per day" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "2 Oct: 5 views" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "3 Oct: 1 view" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show table" }));
    const row = screen.getByRole("row", { name: /2 Oct/ });
    expect(
      within(row)
        .getAllByRole("cell")
        .map((c) => c.textContent),
    ).toEqual(["5", "2", "0", "0", "0"]);
  });
});

describe("the Analytics page", () => {
  beforeEach(() => {
    archive = { namespace: "podcasts", admin: false, loaded: true, can: () => true, isPartial: () => false };
    api.collections.mockReturnValue(ok([{ id: 1, name: "Season 2", path: ["General", "Season 2"], role: null }]));
  });

  it("shows an owner the namespace's numbers, days, collections and resources", async () => {
    api.getAnalytics.mockReturnValue(ok(report()));
    wrap(<AnalyticsScreen />);
    expect(await screen.findByRole("table", { name: "Most used resources" })).toBeInTheDocument();
    expect(api.getAnalytics).toHaveBeenCalledWith(
      expect.objectContaining({ query: { ns: "podcasts", collection: undefined, days: 30 } }),
    );
    expect(screen.getByText("and 2 without an account")).toBeInTheDocument();
    expect(
      within(screen.getByRole("table", { name: "By collection" })).getByText("General › Season 2"),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "At sea" })).toHaveAttribute("href", "/resources/7");
    // another range, and one collection
    fireEvent.click(screen.getByRole("radio", { name: "7 days" }));
    await waitFor(() =>
      expect(api.getAnalytics).toHaveBeenLastCalledWith(
        expect.objectContaining({ query: expect.objectContaining({ days: 7 }) }),
      ),
    );
    fireEvent.change(await screen.findByRole("combobox"), { target: { value: "1" } });
    await waitFor(() =>
      expect(api.getAnalytics).toHaveBeenLastCalledWith(
        expect.objectContaining({ query: { ns: "podcasts", collection: 1, days: 7 } }),
      ),
    );
  });

  it("says so when there's nothing yet", async () => {
    api.getAnalytics.mockReturnValue(
      ok(report({ totals: zero, people: 0, anonymous: 0, collections: [], resources: [] })),
    );
    wrap(<AnalyticsScreen />);
    expect(await screen.findByText("Nothing in this range yet")).toBeInTheDocument();
    expect(screen.getByText("with an account")).toBeInTheDocument();
  });

  it("tells people who aren't owners who is, and asks nothing of the server", async () => {
    archive = { ...archive, can: () => false };
    api.collections.mockReturnValue(ok([]));
    wrap(<AnalyticsScreen />);
    expect(await screen.findByText("Owners of podcasts can see its analytics.")).toBeInTheDocument();
    expect(api.getAnalytics).not.toHaveBeenCalled();
  });

  it("shows admins every namespace, and an error when the server refuses", async () => {
    archive = { ...archive, namespace: null, admin: true };
    api.getAnalytics.mockReturnValue(
      ok(report({ namespace: null, collections: null, namespaces: [{ name: "podcasts", ...zero, view: 6 }] })),
    );
    const all = wrap(<AnalyticsScreen />);
    expect(await screen.findByRole("table", { name: "By namespace" })).toBeInTheDocument();
    expect(screen.getByText("All namespaces")).toBeInTheDocument();
    all.unmount();
    api.getAnalytics.mockReturnValue(fail(403, "needs owner access to this namespace"));
    wrap(<AnalyticsScreen />);
    expect(await screen.findByText("needs owner access to this namespace")).toBeInTheDocument();
  });
});

describe("your activity", () => {
  it("lists what you did, linking what you can still open", async () => {
    api.myActivity.mockReturnValue(
      ok([
        { at: "2026-10-03T10:00:00+00:00", action: "view", resource: 7, title: "At sea", namespace: "podcasts" },
        { at: "2026-10-03T09:00:00+00:00", action: "search", resource: null, title: null, namespace: "podcasts" },
        { at: "2026-10-02T09:00:00+00:00", action: "download", resource: 9, title: null, namespace: null },
      ]),
    );
    wrap(<ActivityPage />);
    const list = await screen.findByRole("list", { name: "Your activity" });
    expect(within(list).getByRole("link", { name: "At sea" })).toHaveAttribute("href", "/resources/7");
    expect(within(list).getByText("a resource you can no longer open")).toBeInTheDocument();
    expect(within(list).getAllByRole("listitem")).toHaveLength(3);
    expect(screen.queryByRole("button", { name: "Show earlier" })).not.toBeInTheDocument();
  });

  it("says when there's nothing, and when it can't be loaded", async () => {
    api.myActivity.mockReturnValue(ok([]));
    const none = wrap(<ActivityPage />);
    expect(await screen.findByText("Nothing yet")).toBeInTheDocument();
    none.unmount();
    api.myActivity.mockReturnValue(fail(500, "boom"));
    wrap(<ActivityPage />);
    expect(await screen.findByText("Couldn’t load your activity")).toBeInTheDocument();
  });
});

describe("reporting a play", () => {
  it("tells the server once, when playing starts", async () => {
    api.played.mockReturnValue(ok(undefined));
    api.playedPublic.mockImplementation(() => Promise.reject(new Error("offline")));
    playing = false;
    const page = wrap(<ReportPlay rid={7} />);
    expect(api.played).not.toHaveBeenCalled();
    playing = true;
    page.rerender(
      <QueryClientProvider client={new QueryClient()}>
        <ReportPlay rid={7} />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(api.played).toHaveBeenCalledTimes(1));
    expect(api.played).toHaveBeenCalledWith(expect.objectContaining({ path: { rid: 7 } }));
    page.unmount();
    // a visitor's page reports through the public route; a failure there never shows
    await act(async () => {
      wrap(<ReportPlay rid={8} visitor />);
    });
    expect(api.playedPublic).toHaveBeenCalledWith(expect.objectContaining({ path: { rid: 8 } }));
    playing = false;
  });
});
