import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";
import type { ReactNode } from "react";

import { Resources, Search } from "@/app/openapi-client";
import type { SearchResults } from "@/app/openapi-client/types.gen";
import { SearchPage } from "@/components/search/search-page";
import { ToastProvider } from "@/components/ui/toast";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Search: { searchTranscripts: jest.fn(), searchPhotos: jest.fn(), suggestTerms: jest.fn() },
  Searches: { listSearches: jest.fn(), deleteSearch: jest.fn(), createSearch: jest.fn() },
  Speakers: { listSpeakers: jest.fn() },
  Resources: { listRecordings: jest.fn(), listTags: jest.fn(), listOrigins: jest.fn(), listLanguages: jest.fn() },
  Sources: {},
  Collections: {},
}));
let mockSearch = "";
const mockPush = jest.fn();
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, replace: jest.fn() }),
  usePathname: () => "/search",
  useSearchParams: () => new URLSearchParams(mockSearch),
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("@/lib/hooks/session", () => ({
  ...jest.requireActual("@/lib/hooks/session"),
  useArchive: () => ({ namespaces: [], can: () => true }),
}));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const m = (f: unknown) => f as jest.Mock;
const NONE: SearchResults = {
  q: "capsid",
  query: "capsid",
  total: 0,
  capped: false,
  hits: [],
  mode: "keyword",
  meaning: null,
  semantic: false,
};

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
  jest.clearAllMocks();
  m(Search.searchTranscripts).mockImplementation(() => ok(NONE));
  m(Search.searchPhotos).mockImplementation(() => ok({ hits: [] }));
  m(Resources.listRecordings).mockImplementation(() => ok([]));
  m(Resources.listTags).mockImplementation(() => ok([{ tag: "interview", recordings: 3 }]));
  m(Resources.listOrigins).mockImplementation(() =>
    ok([
      { origin: "upload", name: "Uploads", recordings: 4 },
      { origin: "source:2", name: "Team drive", recordings: 2 },
    ]),
  );
  m(Resources.listLanguages).mockImplementation(() =>
    ok([
      { language: "en", recordings: 5 },
      { language: "de", recordings: 1 },
    ]),
  );
  const { Searches } = jest.requireMock("@/app/openapi-client");
  m(Searches.listSearches).mockImplementation(() => ok([]));
});

const asked = () => m(Search.searchTranscripts).mock.calls.map(([o]) => o.query);

describe("search by date, tag, source and language", () => {
  it("starts with none set, so the search is as it was, and picking one puts it in the address", async () => {
    mockSearch = "q=capsid";
    wrap(<SearchPage />);
    await waitFor(() => expect(asked().length).toBeGreaterThan(0));
    expect(asked()[0]).toMatchObject({
      q: "capsid",
      from: undefined,
      to: undefined,
      tag: undefined,
      origin: undefined,
    });
    expect(asked()[0].language).toBeUndefined();
    expect(screen.getByLabelText("From")).toHaveValue("");
    expect(screen.getByLabelText("To")).toHaveValue("");
    expect(await screen.findByRole("option", { name: "interview (3)" })).toBeInTheDocument();
    expect(screen.getByLabelText("Tag")).toHaveValue("");

    // one pick each: the list searches again at once, a date once it's a whole day
    fireEvent.change(screen.getByLabelText("Tag"), { target: { value: "interview" } });
    expect(mockPush).toHaveBeenLastCalledWith("/search?q=capsid&tag=interview");
    fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-01-01" } });
    expect(mockPush).toHaveBeenLastCalledWith("/search?q=capsid&from=2026-01-01");
    await screen.findByRole("option", { name: "Team drive (2)" });
    fireEvent.change(screen.getByLabelText("Source"), { target: { value: "source:2" } });
    expect(mockPush).toHaveBeenLastCalledWith("/search?q=capsid&origin=source%3A2");
    fireEvent.change(screen.getByLabelText("Language"), { target: { value: "de" } });
    expect(mockPush).toHaveBeenLastCalledWith("/search?q=capsid&language=de");
  });

  it("asks with the ones in the address, shows each as a chip, and says which to clear when nothing matches", async () => {
    mockSearch = "q=capsid&from=2026-01-01&to=2026-03-31&tag=interview&origin=upload&language=de";
    wrap(<SearchPage />);
    await waitFor(() => expect(asked().some((q) => q.tag)).toBe(true));
    expect(asked().find((q) => q.tag)).toMatchObject({
      q: "capsid",
      from: "2026-01-01",
      to: "2026-03-31",
      tag: ["interview"],
      origin: ["upload"],
      language: ["de"],
      facets: false,
    });
    // the facets and "without filters" count come from the words alone
    expect(asked().some((q) => q.facets && !q.tag && !q.from)).toBe(true);
    expect(screen.getByLabelText("From")).toHaveValue("2026-01-01");
    expect((await screen.findAllByText("date: 2026-01-01 – 2026-03-31")).length).toBeGreaterThan(0);
    expect((await screen.findAllByText("source: Uploads")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("language: German").length).toBeGreaterThan(0);
    expect(screen.getAllByText("tag: interview").length).toBeGreaterThan(0);

    fireEvent.click(await screen.findByRole("button", { name: "Any time" }));
    expect(mockPush).toHaveBeenLastCalledWith("/search?q=capsid&tag=interview&origin=upload&language=de");
    fireEvent.click(screen.getByRole("button", { name: "Any tag" }));
    expect(mockPush).toHaveBeenLastCalledWith(
      "/search?q=capsid&from=2026-01-01&to=2026-03-31&origin=upload&language=de",
    );
  });
});
