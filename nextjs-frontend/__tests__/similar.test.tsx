import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Search } from "@/app/openapi-client";
import type { SimilarResults } from "@/app/openapi-client/types.gen";
import { basisFromParams, basisQuery, citeHref, similarHref, TEXT_MAX } from "@/components/similar/model";
import { SimilarResultsView } from "@/components/similar/similar-list";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({ Search: { findSimilar: jest.fn() } }));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
const findSimilar = Search.findSimilar as jest.Mock;

const RES: SimilarResults = {
  basis: "recording",
  label: "ep1",
  text: null,
  terms: ["capsid", "gene"],
  mode: "words",
  meaning: null,
  semantic: false,
  total: 12,
  items: [
    {
      recording_id: 2,
      title: "ep2",
      namespace: "pods",
      recorded_at: "2026-10-01T10:00:00",
      media_kind: "document",
      score: 0.03,
      match: ["words", "topics"],
      similarity: null,
      cites: [
        { match: "words", source: "page", snippet: "the <mark>capsid</mark> &amp; more", idx: 4, t0: 2000, page: 2 },
        { match: "words", source: "said", snippet: "a <mark>gene</mark>", idx: 7, t0: 65000, page: null },
      ],
      words: ["capsid", "gene"],
      topics: [{ id: 9, label: "Gene therapy" }],
      entities: [{ id: 5, name: "Dyno Therapeutics" }],
    },
  ],
};

function show(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>{ui}</TooltipProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => jest.clearAllMocks());

describe("similar content", () => {
  it("asks for one basis, and links to the full list and the moments", () => {
    expect(basisQuery({ text: "x".repeat(TEXT_MAX + 50), exclude: 3 })).toEqual({ text: "x".repeat(TEXT_MAX), exclude: 3 });
    expect(basisQuery({ topic: 4 })).toEqual({ topic: 4 });
    expect(similarHref({ text: "gene therapy", exclude: 3 })).toBe("/similar?text=gene+therapy&exclude=3");
    expect(similarHref({ text: "gene therapy" })).toBe("/similar?text=gene+therapy");
    expect(similarHref({ entity: 5 })).toBe("/similar?entity=5");
    expect(basisFromParams(new URLSearchParams("text=gene+therapy&exclude=3"))).toEqual({ text: "gene therapy", exclude: 3 });
    expect(basisFromParams(new URLSearchParams("recording=12"))).toEqual({ recording: 12 });
    expect(basisFromParams(new URLSearchParams("topic=x"))).toBeNull();
    expect(basisFromParams(new URLSearchParams(""))).toBeNull();
    // a page of a document opens at that page; a moment at its second
    expect(citeHref(2, { page: 2, t0: 2000 })).toBe("/resources/2?page=2");
    expect(citeHref(2, { page: null, t0: 65000 })).toBe("/resources/2?t=65");
  });

  it("lists what's alike with why, the moments to open, and Find more", async () => {
    findSimilar.mockResolvedValue({ data: RES, response: { ok: true, status: 200 } });
    show(<SimilarResultsView basis={{ recording: 1 }} />);
    expect(await screen.findByRole("link", { name: "ep2" })).toHaveAttribute("href", "/resources/2");
    expect(findSimilar.mock.calls[0][0].query).toEqual({ recording: 1, limit: 10 });
    expect(screen.getByText("Shares key words")).toBeInTheDocument();
    expect(screen.getByText("Same topics")).toBeInTheDocument();
    // the snippet is text, its matches marked, its entities decoded
    expect(screen.getByText("capsid").tagName).toBe("MARK");
    expect(screen.getByText("& more")).toBeInTheDocument();
    expect(screen.getByText("p. 3").closest("a")).toHaveAttribute("href", "/resources/2?page=2");
    expect(screen.getByText("1:05").closest("a")).toHaveAttribute("href", "/resources/2?t=65");
    expect(screen.getByRole("link", { name: /Gene therapy/ })).toHaveAttribute("href", "/topics/9");
    expect(screen.getByRole("link", { name: /Dyno Therapeutics/ })).toHaveAttribute("href", "/entities/5");
    expect(screen.getByText("By key words, topics and entities.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Find more \(12\)/ })).toHaveAttribute("href", "/similar?recording=1");
  });

  it("says when nothing is alike, and why it wasn't by meaning", async () => {
    findSimilar.mockResolvedValue({ data: { ...RES, items: [], total: 0 }, response: { ok: true, status: 200 } });
    show(<SimilarResultsView basis={{ text: "nothing like it" }} compact />);
    expect(await screen.findByText("Nothing similar found.")).toBeInTheDocument();
  });
});
