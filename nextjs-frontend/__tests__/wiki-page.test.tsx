import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Entities } from "@/app/openapi-client";
import { WikiPage } from "@/components/wiki/entity-page";
import { addressOf, samePath, titleOf, wikiPath, wikiSlug } from "@/components/wiki/links";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Entities: {
    findWikiPage: jest.fn(),
    getEntity: jest.fn(),
    listEntityMentions: jest.fn(),
    getEntityConnections: jest.fn(),
  },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("@/components/ui/toast", () => ({ useToast: () => jest.fn() }));
jest.mock("@/lib/hooks/session", () => ({ useArchive: () => ({ can: () => true }) }));
// the notes part and "Similar content" are tested on their own; here they only have to be there
jest.mock("@/components/notes/note-page", () => ({
  NotePage: ({ about, embedded }: { about: string; embedded?: boolean }) => (
    <div data-testid="notes">{`${about} ${embedded ? "embedded" : ""}`}</div>
  ),
}));
jest.mock("@/components/similar/similar-list", () => ({ SimilarSection: () => null }));
const replace = jest.fn();
jest.mock("next/navigation", () => ({ useRouter: () => ({ replace, push: jest.fn() }) }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const m = (f: unknown) => f as jest.Mock;
const show = (ui: React.ReactNode) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>{ui}</TooltipProvider>
    </QueryClientProvider>,
  );
};

const ADA = {
  id: 5,
  name: "Ada Lovelace",
  key: "ada lovelace",
  type: "PERSON",
  type_label: "Person",
  namespace: "work",
  description: "Wrote the first program",
  aliases: ["Countess of Lovelace"],
  mentions: 3,
  recordings: 2,
  speakers: 1,
  first: "2026-01-02",
  last: "2026-03-04",
  links: [{ id: 9, name: "Ada Lovelace", namespace: "home" }],
  page: "/wiki/work/Ada_Lovelace",
};

beforeEach(() => {
  jest.clearAllMocks();
  window.history.replaceState(null, "", "/wiki/work/Ada_Lovelace");
  m(Entities.getEntity).mockReturnValue(ok(ADA));
  m(Entities.listEntityMentions).mockReturnValue(
    ok({
      total: 1,
      items: [
        {
          mention: 1,
          recording_id: 12,
          title: "Weekly call",
          t0: 65000,
          time: "01:05",
          speaker: "Alice",
          text: "We read Ada Lovelace's notes.",
          highlight: [8, 20],
        },
      ],
    }),
  );
  m(Entities.getEntityConnections).mockReturnValue(
    ok({
      entities: [{ id: 6, name: "Charles Babbage", type: "PERSON", together: 2 }],
      speakers: [{ id: 3, name: "Alice", mentions: 3 }],
      recordings: [{ id: 12, title: "Weekly call", mentions: 2 }],
    }),
  );
});

describe("wiki addresses", () => {
  it("name a page by the entity's name, spaces as underscores", () => {
    expect(wikiSlug("  Ada   Lovelace ")).toBe("Ada_Lovelace");
    expect(wikiPath("work", "Ada Lovelace")).toBe("/wiki/work/Ada_Lovelace");
    expect(wikiPath("work", "AC/DC?")).toBe("/wiki/work/AC%2FDC%3F");
    expect(addressOf(["AC%2FDC%3F"])).toBe("AC/DC?");
    expect(addressOf(["AC", "DC"])).toBe("AC/DC");
    expect(addressOf(["100%"])).toBe("100%"); // not escaped: kept as it is
    expect(titleOf("Ada_Lovelace")).toBe("Ada Lovelace");
    expect(samePath("/wiki/a/McDonald%27s", "/wiki/a/McDonald's")).toBe(true);
    expect(samePath("/wiki/a/Ada_Lovelace", "/wiki/a/ada_lovelace")).toBe(false);
  });
});

describe("an entity's page", () => {
  it("shows what it is, its notes, where it's said and what it comes up with", async () => {
    m(Entities.findWikiPage).mockReturnValue(
      ok({ kind: "entity", id: 5, namespace: "work", title: "Ada Lovelace", path: ADA.page, canonical: true }),
    );
    show(<WikiPage ns="work" address="Ada_Lovelace" />);
    expect(await screen.findByRole("heading", { level: 1, name: "Ada Lovelace" })).toBeInTheDocument();
    expect(m(Entities.findWikiPage).mock.calls[0][0].path).toEqual({ ns: "work", address: "Ada_Lovelace" });
    expect(screen.getByText("Wrote the first program")).toBeInTheDocument();
    expect(screen.getByText("Also said as Countess of Lovelace")).toBeInTheDocument();
    expect(screen.getByTestId("notes")).toHaveTextContent("entity:5 embedded");

    const said = screen.getByRole("region", { name: "Where it’s said" });
    expect(await within(said).findByRole("link", { name: "Weekly call · 01:05 · Alice" })).toHaveAttribute(
      "href",
      "/resources/12?t=65",
    );
    expect(within(said).getByText("Ada Lovelace", { selector: "mark" })).toBeInTheDocument();

    const side = screen.getByRole("complementary", { name: "About this entity" });
    expect(await within(side).findByRole("link", { name: "Charles Babbage" })).toHaveAttribute(
      "href",
      "/wiki/work/Charles_Babbage",
    );
    expect(within(side).getByRole("link", { name: "Ada Lovelace" })).toHaveAttribute("href", "/wiki/home/Ada_Lovelace");
    expect(within(side).getByRole("link", { name: "Alice" })).toHaveAttribute("href", "/speakers/3");
    expect(screen.getByRole("link", { name: "Graph" })).toHaveAttribute("href", "/graph?focus=e5");
    expect(replace).not.toHaveBeenCalled();
  });

  it("switches another way of writing its name to its own address", async () => {
    window.history.replaceState(null, "", "/wiki/work/ada_lovelace");
    m(Entities.findWikiPage).mockReturnValue(
      ok({ kind: "entity", id: 5, namespace: "work", title: "Ada Lovelace", path: ADA.page, canonical: false }),
    );
    show(<WikiPage ns="work" address="ada_lovelace" />);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/wiki/work/Ada_Lovelace"));
  });

  it("says when nothing is called that", async () => {
    m(Entities.findWikiPage).mockReturnValue(
      Promise.resolve({ error: { detail: "not found" }, response: { ok: false, status: 404 } }),
    );
    show(<WikiPage ns="work" address="Nobody" />);
    expect(await screen.findByText("No page by that name")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Entities in work" })).toHaveAttribute("href", "/entities?ns=work");
  });
});
