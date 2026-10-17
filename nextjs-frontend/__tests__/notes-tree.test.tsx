import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Notes } from "@/app/openapi-client";
import { NotesTree } from "@/components/notes/notes-tree";

jest.mock("@/app/openapi-client", () => ({
  Notes: {
    listPages: jest.fn(),
    createPage: jest.fn(),
    movePage: jest.fn(),
    updatePage: jest.fn(),
    deletePage: jest.fn(),
    restoreDeleted: jest.fn(),
    pinPage: jest.fn(),
    unpinPage: jest.fn(),
  },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
const push = jest.fn();
let pathname = "/notes";
jest.mock("next/navigation", () => ({ usePathname: () => pathname, useRouter: () => ({ push }) }));
const toast = jest.fn();
jest.mock("@/components/ui/toast", () => ({ useToast: () => toast }));
const download = jest.fn<Promise<void>, [string, string | undefined, string]>(() => Promise.resolve());
jest.mock("@/components/library/actions", () => ({
  downloadWithToken: (url: string, token: string | undefined, name: string) => download(url, token, name),
}));
let editor = true;
jest.mock("@/lib/hooks/session", () => ({
  useArchive: () => ({ namespaces: [{ name: "home" }], namespace: "home", can: () => editor }),
}));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const m = (f: unknown) => f as jest.Mock;
const show = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <NotesTree />
    </QueryClientProvider>,
  );

const PAGES = [
  { id: 1, title: "Home lab", author: "person" },
  { id: 2, title: "Pi 5 shopping list", author: "person", parent: 1 },
  { id: 3, title: "NVMe hat options", author: "person", parent: 2 },
  { id: 4, title: "Trips", author: "person" },
];

/** Open a row's ⋯ menu and choose an item (items run once the menu has closed). */
async function choose(row: string, item: string) {
  fireEvent.keyDown(await screen.findByRole("button", { name: `More for ${row}` }), { key: "Enter" });
  fireEvent.click(await screen.findByRole("menuitem", { name: item }));
}

beforeAll(() => {
  Element.prototype.scrollIntoView = jest.fn();
});
beforeEach(() => {
  jest.clearAllMocks();
  localStorage.clear();
  pathname = "/notes";
  editor = true;
  m(Notes.listPages).mockImplementation(() => ok({ namespace: "home", pages: PAGES }));
});

describe("the notes tree", () => {
  it("renames a note in place: Enter saves, Escape leaves it as it was", async () => {
    m(Notes.updatePage).mockImplementation(() => ok({ id: 4, title: "Holidays" }));
    show();
    await choose("Trips", "Rename");
    const box = await screen.findByRole("textbox", { name: "New name for Trips" });
    expect(box).toHaveValue("Trips");
    fireEvent.change(box, { target: { value: "Holidays" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(Notes.updatePage).toHaveBeenCalled());
    expect(m(Notes.updatePage).mock.calls[0][0]).toMatchObject({ path: { pid: 4 }, body: { title: "Holidays" } });

    await choose("Home lab", "Rename");
    const again = await screen.findByRole("textbox", { name: "New name for Home lab" });
    fireEvent.change(again, { target: { value: "Lab" } });
    fireEvent.keyDown(again, { key: "Escape" });
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(Notes.updatePage).toHaveBeenCalledTimes(1);
  });

  it("moves a note into another found by name, never into itself or a note inside it", async () => {
    m(Notes.movePage).mockImplementation(() => ok({ ok: true }));
    show();
    await choose("Home lab", "Move to…");
    const dialog = await screen.findByRole("dialog", { name: "Move “Home lab”" });
    // Home lab is at the top already, and Pi 5 shopping list is inside it
    expect(
      within(dialog)
        .getAllByRole("option")
        .map((o) => o.textContent),
    ).toEqual(["Trips"]);
    fireEvent.keyDown(within(dialog).getByRole("combobox", { name: "Move into" }), { key: "Enter" });
    await waitFor(() => expect(Notes.movePage).toHaveBeenCalled());
    expect(m(Notes.movePage).mock.calls[0][0]).toMatchObject({ path: { pid: 1 }, body: { parent: 4 } });
  });

  it("moves a nested note to the top with one pick", async () => {
    localStorage.setItem("lens.notes.open", "[1]");
    m(Notes.movePage).mockImplementation(() => ok({ ok: true }));
    show();
    await choose("Pi 5 shopping list", "Move to the top");
    await waitFor(() => expect(Notes.movePage).toHaveBeenCalled());
    expect(m(Notes.movePage).mock.calls[0][0]).toMatchObject({ path: { pid: 2 }, body: { parent: null } });
  });

  it("adds a note inside another from the menu and opens it", async () => {
    m(Notes.createPage).mockImplementation(() => ok({ id: 9, title: "Untitled" }));
    show();
    await choose("Trips", "Add a note inside");
    await waitFor(() => expect(push).toHaveBeenCalledWith("/notes/9?new=1"));
    expect(m(Notes.createPage).mock.calls[0][0].body).toMatchObject({ ns: "home", parent: 4 });
  });

  it("deletes a note with Undo on the notice, and leaves it when it was open", async () => {
    pathname = "/notes/4";
    m(Notes.deletePage).mockImplementation(() => ok({ ok: true }));
    show();
    await choose("Trips", "Delete");
    await waitFor(() => expect(push).toHaveBeenCalledWith("/notes"));
    expect(m(Notes.deletePage).mock.calls[0][0].path).toEqual({ pid: 4 });
    expect(toast.mock.calls[0][0]).toMatchObject({ title: "Deleted “Trips”", action: { label: "Undo" } });
  });

  it("pins a note, which then shows at the top, and unpins it there", async () => {
    m(Notes.pinPage).mockImplementation(() => ok({ id: 4, title: "Trips", pinned: true }));
    m(Notes.unpinPage).mockImplementation(() => ok({ id: 4, title: "Trips", pinned: false }));
    show();
    await choose("Trips", "Pin");
    const pinned = await screen.findByRole("group", { name: "Pinned notes" });
    expect(within(pinned).getByRole("link", { name: "Trips" })).toHaveAttribute("href", "/notes/4");
    await waitFor(() => expect(Notes.pinPage).toHaveBeenCalled());
    expect(m(Notes.pinPage).mock.calls[0][0].path).toEqual({ pid: 4 });
    fireEvent.click(within(pinned).getByRole("button", { name: "Unpin Trips" }));
    await waitFor(() => expect(screen.queryByRole("group", { name: "Pinned notes" })).not.toBeInTheDocument());
    expect(m(Notes.unpinPage).mock.calls[0][0].path).toEqual({ pid: 4 });
  });

  it("lets someone who can only read pin and download, and nothing else", async () => {
    editor = false;
    show();
    fireEvent.keyDown(await screen.findByRole("button", { name: "More for Trips" }), { key: "Enter" });
    const items = await screen.findAllByRole("menuitem");
    expect(items.map((i) => i.textContent)).toEqual(["Pin", "Download as Markdown"]);
    expect(screen.queryByRole("button", { name: "New note inside Trips" })).not.toBeInTheDocument();
  });

  it("takes a pin back when the server says no", async () => {
    m(Notes.pinPage).mockImplementation(() =>
      Promise.resolve({ error: { detail: "Up to 50 notes can be pinned" }, response: { ok: false, status: 400 } }),
    );
    show();
    await choose("Trips", "Pin");
    await waitFor(() => expect(toast).toHaveBeenCalled());
    expect(toast.mock.calls[0][0].title).toBe("Couldn’t pin it");
    expect(screen.queryByRole("group", { name: "Pinned notes" })).not.toBeInTheDocument();
  });

  it("downloads a note as Markdown", async () => {
    show();
    await choose("Trips", "Download as Markdown");
    await waitFor(() => expect(download).toHaveBeenCalledWith("/api/v1/notes/4/markdown", "t", "Trips.md"));
  });
});
