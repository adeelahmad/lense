import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Notes } from "@/app/openapi-client";
import { NotePage, wordCount } from "@/components/notes/note-page";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Notes: {
    getPage: jest.fn(),
    createPage: jest.fn(),
    updatePage: jest.fn(),
    deletePage: jest.fn(),
    restoreDeleted: jest.fn(),
    pinPage: jest.fn(),
    unpinPage: jest.fn(),
  },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
const push = jest.fn();
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
  useSearchParams: () => new URLSearchParams(),
}));
// the editor loads BlockSuite in a browser; here it only has to be there
jest.mock("next/dynamic", () => () => () => <div data-testid="editor" />);
const toast = jest.fn();
jest.mock("@/components/ui/toast", () => ({ useToast: () => toast }));
const download = jest.fn<Promise<void>, [string, string | undefined, string]>(() => Promise.resolve());
jest.mock("@/components/library/actions", () => ({
  downloadWithToken: (url: string, token: string | undefined, name: string) => download(url, token, name),
}));
jest.mock("@/components/costs/costs", () => ({ ActivityPanel: () => <div data-testid="activity" /> }));
jest.mock("@/components/notes/home-suggestions", () => ({ HomeSuggestion: () => null }));
jest.mock("@/components/notes/link-suggestions", () => ({ LinkSuggestions: () => null }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const m = (f: unknown) => f as jest.Mock;
const show = (ui: React.ReactNode) =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <TooltipProvider>{ui}</TooltipProvider>
    </QueryClientProvider>,
  );

const PAGE = {
  id: 2,
  title: "Pi 5 shopping list",
  namespace: "home",
  author: "person",
  body: "Parts for @[Home lab](page:1).\n\n1. Pi 5 8GB\n2. NVMe hat",
  parent: 1,
  pinned: false,
  can_edit: true,
  links: [],
  backlinks: [],
  path: [{ id: 1, title: "Home lab" }],
  children: [{ id: 3, title: "NVMe hat options", updated_at: "2026-10-15T11:50:00Z" }],
};

/** Open the note's ⋯ menu and choose an item (items run once the menu has closed). */
async function choose(item: string) {
  fireEvent.keyDown(await screen.findByRole("button", { name: "More on this note" }), { key: "Enter" });
  fireEvent.click(await screen.findByRole("menuitem", { name: item }));
}

beforeEach(() => {
  jest.clearAllMocks();
  m(Notes.getPage).mockImplementation(() => ok(PAGE));
});

describe("word count", () => {
  it("counts the words a reader sees", () => {
    expect(wordCount(PAGE.body)).toBe(9);
    expect(wordCount("- [ ] Flash the SD card\n- [x] Pick a cloud model")).toBe(8);
    expect(wordCount("## Plan\n\n**well-known** words — here")).toBe(4);
    expect(wordCount("")).toBe(0);
  });
});

describe("a note's page", () => {
  it("says where it is, with each note it's inside a link", async () => {
    show(<NotePage id={2} />);
    const crumbs = await screen.findByRole("navigation", { name: "Where this note is" });
    expect(within(crumbs).getByRole("link", { name: "Notes" })).toHaveAttribute("href", "/notes");
    expect(within(crumbs).getByText("home")).toBeInTheDocument();
    expect(within(crumbs).getByRole("link", { name: "Home lab" })).toHaveAttribute("href", "/notes/1");
    expect(screen.getByText("9 words")).toBeInTheDocument();
  });

  it("lists the notes inside it, and Add makes one there with its title ready", async () => {
    m(Notes.createPage).mockImplementation(() => ok({ ...PAGE, id: 9, title: "Untitled", parent: 2 }));
    show(<NotePage id={2} />);
    expect(await screen.findByRole("link", { name: "NVMe hat options" })).toHaveAttribute("href", "/notes/3");
    fireEvent.click(screen.getByRole("button", { name: "Add a note inside" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/notes/9?new=1"));
    expect(m(Notes.createPage).mock.calls[0][0].body).toEqual({ ns: "home", title: "Untitled", parent: 2 });
  });

  it("keeps costs and activity folded until opened", async () => {
    show(<NotePage id={2} />);
    const fold = await screen.findByText("Costs and activity");
    expect(screen.queryByTestId("activity")).not.toBeInTheDocument();
    const details = fold.closest("details")!;
    details.open = true;
    fireEvent(details, new Event("toggle"));
    expect(await screen.findByTestId("activity")).toBeInTheDocument();
  });

  it("has History, Pin, Download and Delete in its menu", async () => {
    m(Notes.pinPage).mockImplementation(() => ok({ ok: true }));
    m(Notes.deletePage).mockImplementation(() => ok({ ok: true }));
    show(<NotePage id={2} />);
    await choose("Pin");
    await waitFor(() => expect(m(Notes.pinPage).mock.calls[0][0].path).toEqual({ pid: 2 }));
    await choose("Download as Markdown");
    await waitFor(() =>
      expect(download).toHaveBeenCalledWith("/api/v1/notes/2/markdown", "t", "Pi 5 shopping list.md"),
    );
    await choose("Delete");
    await waitFor(() => expect(push).toHaveBeenCalledWith("/notes"));
    expect(toast.mock.calls[0][0].action.label).toBe("Undo");
  });

  it("shows a viewer the notes inside but not Add, Delete or Add inside", async () => {
    m(Notes.getPage).mockImplementation(() => ok({ ...PAGE, can_edit: false }));
    show(<NotePage id={2} />);
    expect(await screen.findByRole("link", { name: "NVMe hat options" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add a note inside" })).not.toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole("button", { name: "More on this note" }), { key: "Enter" });
    const items = (await screen.findAllByRole("menuitem")).map((i) => i.textContent);
    expect(items).toEqual(["Pin", "History", "Download as Markdown"]);
  });
});
