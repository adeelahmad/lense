import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Notes } from "@/app/openapi-client";
import { CommandPalette } from "@/components/app-shell/command-palette";
import { NotesHome } from "@/components/notes/notes-home";

jest.mock("@/app/openapi-client", () => ({
  Notes: {
    listPages: jest.fn(),
    searchPages: jest.fn(),
    createPage: jest.fn(),
    deletedPages: jest.fn(() => Promise.resolve({ data: [], response: { ok: true, status: 200 } })),
  },
  Resources: { listRecordings: jest.fn(() => Promise.resolve({ data: [], response: { ok: true, status: 200 } })) },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
const push = jest.fn();
jest.mock("next/navigation", () => ({ usePathname: () => "/notes", useRouter: () => ({ push }) }));
jest.mock("@/components/ui/toast", () => ({ useToast: () => jest.fn() }));
jest.mock("@/lib/hooks/session", () => ({
  useArchive: () => ({
    admin: false,
    namespaces: [{ name: "home" }],
    namespace: "home",
    can: () => true,
  }),
}));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const m = (f: unknown) => f as jest.Mock;
const show = (ui: React.ReactNode) =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      {ui}
    </QueryClientProvider>,
  );

const page = (id: number, title: string, extra: Record<string, unknown> = {}) => ({
  id,
  title,
  author: "person",
  updated_at: `2026-10-${10 + id}T10:00:00Z`,
  ...extra,
});

beforeEach(() => {
  jest.clearAllMocks();
  m(Notes.listPages).mockImplementation(() =>
    ok({
      namespace: "home",
      pages: [
        page(1, "Home lab", { place: "project" }),
        page(2, "Pi 5 shopping list", { parent: 1 }),
        page(3, "NVMe hat options", { parent: 2 }),
        page(4, "Old trip", { place: "archive" }),
      ],
    }),
  );
});

describe("the Notes page", () => {
  it("shows where each note sits in the tree, and one PARA place at a time", async () => {
    show(<NotesHome />);
    const hat = await screen.findByRole("link", { name: /NVMe hat options/ });
    expect(hat).toHaveTextContent("In Home lab › Pi 5 shopping list");
    expect(hat).toHaveAttribute("href", "/notes/3");
    fireEvent.click(screen.getByRole("radio", { name: /Archive/ }));
    expect(screen.getAllByRole("listitem").map((li) => li.textContent)).toEqual([expect.stringContaining("Old trip")]);
    fireEvent.click(screen.getByRole("radio", { name: /Areas/ }));
    expect(screen.getByText("No notes here")).toBeInTheDocument();
  });

  it("finds notes by their words, with the words around the match, and Enter opens the first", async () => {
    m(Notes.searchPages).mockImplementation(() =>
      ok({
        total: 1,
        pages: [
          {
            id: 9,
            title: "Weekly call",
            about: "entity:5",
            namespace: "home",
            author: "assistant",
            snippet: "…we talked about capsids…",
          },
        ],
      }),
    );
    show(<NotesHome />);
    const box = await screen.findByRole("searchbox", { name: "Search notes" });
    fireEvent.change(box, { target: { value: "capsids" } });
    const hit = await screen.findByRole("link", { name: /Weekly call/ });
    expect(hit).toHaveTextContent("Page of entity");
    expect(hit).toHaveTextContent("…we talked about capsids…");
    expect(hit).toHaveAttribute("href", "/notes/about/entity/5");
    expect(m(Notes.searchPages).mock.calls.at(-1)[0].query).toMatchObject({ q: "capsids", ns: "home", place: null });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(push).toHaveBeenCalledWith("/notes/about/entity/5");
  });

  it("says when no note has the words", async () => {
    m(Notes.searchPages).mockImplementation(() => ok({ total: 0, pages: [] }));
    show(<NotesHome />);
    fireEvent.change(await screen.findByRole("searchbox", { name: "Search notes" }), { target: { value: "zebra" } });
    expect(await screen.findByText("No notes have “zebra”")).toBeInTheDocument();
  });
});

describe("⌘K", () => {
  it("jumps to a note found by its words", async () => {
    m(Notes.searchPages).mockImplementation(() =>
      ok({ total: 1, pages: [{ id: 2, title: "Pi 5 shopping list", namespace: "home", place: "project" }] }),
    );
    show(<CommandPalette />);
    act(() => {
      fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    });
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "shopping" } });
    const option = await screen.findByRole("option", { name: /Pi 5 shopping list/ });
    expect(within(option).getByText("Project")).toBeInTheDocument();
    fireEvent.click(option);
    expect(push).toHaveBeenCalledWith("/notes/2");
    await waitFor(() => expect(screen.queryByRole("combobox")).not.toBeInTheDocument());
  });
});
