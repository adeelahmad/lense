import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";

import type { PublicSearch } from "@/app/openapi-client/types.gen";
import { searchPath } from "@/components/public/model";
import { PublicSearchView } from "@/components/public/search-view";
import { ByMeaning, MEANING_OFF, MeaningToggle } from "@/components/search/meaning";
import { TooltipProvider } from "@/components/ui/tooltip";

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const searchPublic = jest.fn<Promise<unknown>, [{ query: Record<string, unknown> }]>();
jest.mock("@/app/openapi-client", () => ({
  Public: { searchPublic: (a: { query: Record<string, unknown> }) => searchPublic(a) },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: null }) }));
const push = jest.fn();
jest.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
jest.mock("@/components/public/hooks", () => ({
  usePublicClient: () => ({ client: {}, signedIn: false, ready: true }),
}));

const wrap = (ui: React.ReactNode) =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <TooltipProvider delayDuration={0}>{ui}</TooltipProvider>
    </QueryClientProvider>,
  );

describe("the Meaning switch", () => {
  it("switches search by meaning on and off where it's available", () => {
    const onChange = jest.fn();
    wrap(<MeaningToggle checked={false} available onChange={onChange} />);
    const sw = screen.getByRole("switch", { name: "Meaning" });
    expect(sw).not.toBeChecked();
    fireEvent.click(sw);
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("stays visible, disabled, and says why where an admin hasn't switched it on", async () => {
    const onChange = jest.fn();
    wrap(<MeaningToggle checked available={false} onChange={onChange} />);
    const sw = screen.getByRole("switch", { name: "Meaning" });
    expect(sw).toBeDisabled();
    expect(sw).not.toBeChecked(); // an address that asks for meaning doesn't make it look on
    fireEvent.focus(sw.parentElement!.parentElement!);
    expect((await screen.findAllByText(MEANING_OFF)).length).toBeGreaterThan(0);
    fireEvent.click(sw);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("waits, disabled, until the server has said", () => {
    wrap(<MeaningToggle checked={false} available={undefined} onChange={() => {}} />);
    expect(screen.getByRole("switch", { name: "Meaning" })).toBeDisabled();
  });

  it("marks hits found by their meaning", () => {
    wrap(<ByMeaning />);
    expect(screen.getByText("By meaning")).toBeInTheDocument();
  });
});

describe("visitors searching by meaning", () => {
  const result = (over: Partial<PublicSearch> = {}): PublicSearch => ({
    q: "sailors",
    total: 1,
    capped: false,
    semantic: true,
    items: [
      {
        id: 7,
        title: "At sea",
        namespace: "podcasts",
        media_kind: "audio",
        view: "open",
        hits: [{ t0: 4000, snippet: "The ship left the harbour", match: "meaning" }],
      } as unknown as PublicSearch["items"][number],
    ],
    ...over,
  });

  it("keeps meaning in the address", () => {
    expect(searchPath("sailors")).toBe("/explore/search?q=sailors");
    expect(searchPath("sailors", true)).toBe("/explore/search?q=sailors&meaning=1");
    expect(searchPath("  ", true)).toBe("/explore/search");
  });

  it("offers the switch where the archive searches by meaning, and marks what it found", async () => {
    searchPublic.mockReturnValue(ok(result()));
    wrap(<PublicSearchView q="sailors" meaning />);
    expect(await screen.findByText("By meaning")).toBeInTheDocument();
    expect(searchPublic).toHaveBeenCalledWith(
      expect.objectContaining({ query: { q: "sailors", limit: 20, offset: 0, semantic: true } }),
    );
    const sw = screen.getByRole("switch", { name: "Meaning" });
    expect(sw).toBeChecked();
    fireEvent.click(sw);
    expect(push).toHaveBeenCalledWith("/explore/search?q=sailors");
    // searching again keeps it
    fireEvent.change(screen.getByRole("searchbox", { name: "Search the archive" }), { target: { value: "tides" } });
    fireEvent.submit(screen.getByRole("search"));
    expect(push).toHaveBeenCalledWith("/explore/search?q=tides&meaning=1");
  });

  it("offers no switch where it's off", async () => {
    searchPublic.mockReturnValue(ok(result({ semantic: false, items: [] as PublicSearch["items"], total: 0 })));
    wrap(<PublicSearchView q="sailors" />);
    expect(await screen.findByText(/Nothing matches/)).toBeInTheDocument();
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
  });
});
