import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom";
import type { ReactNode } from "react";

import { Namespaces, Recordings } from "@/app/openapi-client";
import type { CollectionNode } from "@/app/openapi-client/types.gen";
import { CollectionField, CollectionsDialog, PlaceDialog } from "@/components/library/collections-ui";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Namespaces: {
    listNamespaceCollections: jest.fn(),
    createNamespaceCollection: jest.fn(),
    updateNamespaceCollection: jest.fn(),
    deleteNamespaceCollection: jest.fn(),
    listCollectionMembers: jest.fn(),
    setCollectionMember: jest.fn(),
  },
  Recordings: { placeRecordings: jest.fn() },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
const toast = jest.fn();
jest.mock("@/components/ui/toast", () => ({ useToast: () => toast }));
let editor = true;
jest.mock("@/lib/hooks/session", () => ({
  ...jest.requireActual("@/lib/hooks/session"),
  useArchive: () => ({ can: () => editor, namespaces: [{ name: "pods" }] }),
}));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const m = (f: unknown) => f as jest.Mock;
const node = (id: number, name: string, parent: number | null, depth: number, over: Partial<CollectionNode> = {}) =>
  ({
    id,
    name,
    parent,
    depth,
    path: [],
    recordings: 0,
    total: 0,
    children: 0,
    default: false,
    ...over,
  }) as CollectionNode;
/** pods: General (default, 3 recordings) · Talks (holds 2024) › 2024 (empty) */
const TREE = [
  node(1, "General", null, 0, { default: true, recordings: 3, total: 3 }),
  node(2, "Talks", null, 0, { children: 1, recordings: 1, total: 1 }),
  node(3, "2024", 2, 1),
];

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>{ui}</TooltipProvider>
    </QueryClientProvider>,
  );
}

/** Open a row's ⋯ menu and choose an item (items run once the menu has closed). */
async function choose(row: string, item: RegExp | string) {
  const trigger = screen.getByRole("button", { name: `Actions for ${row}` });
  fireEvent.keyDown(trigger, { key: "Enter" });
  fireEvent.click(await screen.findByRole("menuitem", { name: item }));
}

beforeEach(() => {
  editor = true;
  // what each collection lets this person do, as the API says it: an editor of the namespace arranges them all
  m(Namespaces.listNamespaceCollections).mockImplementation(() =>
    ok(TREE.map((n) => ({ ...n, role: editor ? "editor" : "viewer", can_change: editor, can_grant: false }))),
  );
});

describe("the collections dialog", () => {
  it("lists the tree and makes, renames, moves, defaults and deletes collections", async () => {
    const onPick = jest.fn();
    wrap(<CollectionsDialog ns="pods" open onOpenChange={() => {}} onPick={onPick} />);
    const list = await screen.findByRole("list", { name: "Collections in pods" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(3);
    expect(within(list).getByText("Default")).toBeInTheDocument();
    expect(within(list).getAllByText("3 recordings").length).toBeGreaterThan(0);

    // a new one at the top
    m(Namespaces.createNamespaceCollection).mockImplementation(() => ok(node(4, "Interviews", null, 0)));
    fireEvent.change(screen.getByLabelText("New collection"), { target: { value: "  Interviews " } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() =>
      expect(m(Namespaces.createNamespaceCollection).mock.calls[0][0]).toMatchObject({
        path: { name: "pods" },
        body: { name: "Interviews" },
      }),
    );
    await waitFor(() => expect(toast).toHaveBeenCalledWith(expect.objectContaining({ title: "Made “Interviews”" })));

    // renamed
    m(Namespaces.updateNamespaceCollection).mockImplementation((o: { path: { cid: number }; body: object }) =>
      ok({ ...TREE.find((n) => n.id === o.path.cid), ...o.body }),
    );
    await choose("Talks", "Rename");
    const name = await screen.findByLabelText("New name for Talks");
    fireEvent.change(name, { target: { value: "Lectures" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(m(Namespaces.updateNamespaceCollection).mock.calls[0][0]).toMatchObject({
        path: { name: "pods", cid: 2 },
        body: { name: "Lectures" },
      }),
    );

    // moved inside another: only where it may go is offered (not inside itself)
    await choose("Talks", "Move to…");
    const where = await screen.findByLabelText("Where to move Talks");
    expect(
      within(where)
        .getAllByRole("option")
        .map((o) => o.textContent),
    ).toEqual(["The top of pods", "General (default)"]);
    fireEvent.change(where, { target: { value: "1" } });
    fireEvent.click(screen.getByRole("button", { name: "Move" }));
    await waitFor(() =>
      expect(m(Namespaces.updateNamespaceCollection).mock.calls[1][0]).toMatchObject({
        path: { cid: 2 },
        body: { parent: 1 },
      }),
    );

    // made the default
    await choose("2024", "Make it the default");
    await waitFor(() =>
      expect(m(Namespaces.updateNamespaceCollection).mock.calls[2][0]).toMatchObject({
        path: { cid: 3 },
        body: { default: true },
      }),
    );

    // deleted, once confirmed; the default and a collection holding others can't be
    const trigger = screen.getByRole("button", { name: "Actions for General" });
    fireEvent.keyDown(trigger, { key: "Enter" });
    expect(await screen.findByRole("menuitem", { name: /default collection/ })).toHaveAttribute("data-disabled");
    fireEvent.keyDown(trigger, { key: "Enter" }); // closes it again
    await waitFor(() => expect(screen.queryByRole("menu")).not.toBeInTheDocument());
    m(Namespaces.deleteNamespaceCollection).mockImplementation(() => ok({ ok: true }));
    await choose("2024", "Delete…");
    fireEvent.click(within(await screen.findByRole("alert")).getByRole("button", { name: "Delete" }));
    await waitFor(() =>
      expect(m(Namespaces.deleteNamespaceCollection).mock.calls[0][0]).toMatchObject({
        path: { name: "pods", cid: 3 },
      }),
    );

    // a name shows that collection in the Library
    fireEvent.click(within(list).getByRole("button", { name: /^General/ }));
    expect(onPick).toHaveBeenCalledWith(1);
  });

  it("is read-only for viewers, and says why", async () => {
    editor = false;
    wrap(<CollectionsDialog ns="pods" open onOpenChange={() => {}} />);
    await screen.findByRole("list", { name: "Collections in pods" });
    expect(screen.getByLabelText("New collection")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Add" })).toHaveAttribute("aria-disabled", "true");
    expect(screen.getAllByText("Editors of pods can do this").length).toBeGreaterThan(0);
  });
});

describe("people in a collection", () => {
  it("gives, changes and takes away roles, and shows the ones from above", async () => {
    // an admin of Talks (but no role in the namespace): arranges Talks and what's inside it, gives roles on them
    editor = false;
    m(Namespaces.listNamespaceCollections).mockImplementation(() =>
      ok([
        node(2, "Talks", null, 0, { role: "admin", can_change: true, can_grant: true, children: 1 }),
        node(3, "2024", 2, 1, { role: "admin", can_change: true, can_grant: true }),
      ]),
    );
    const people = [
      { account: 7, email: "ann@x.io", name: "Ann", role: "viewer" },
      { account: 8, email: "bo@x.io", role: "admin", inherited_from: { id: 2, name: "Talks" } },
    ];
    m(Namespaces.listCollectionMembers).mockImplementation(() => ok(people));
    m(Namespaces.setCollectionMember).mockImplementation(() => ok(people));
    wrap(<CollectionsDialog ns="pods" open onOpenChange={() => {}} />);
    await screen.findByRole("list", { name: "Collections in pods" });
    // the top is the namespace's editors', and says so
    expect(screen.getByRole("button", { name: "Add" })).toHaveAttribute("aria-disabled", "true");
    expect(
      screen.getByText("You arrange the collections you’re an admin of, and the ones inside them."),
    ).toBeInTheDocument();
    await choose("2024", "People…");
    const dialog = await screen.findByRole("dialog", { name: "People in 2024" });
    const list = await within(dialog).findByRole("list", { name: "People in 2024" });
    expect(within(list).getByText("Ann")).toBeInTheDocument();
    expect(within(list).getByText(/Admin through “/)).toHaveTextContent("Admin through “Talks”");
    fireEvent.change(within(dialog).getByLabelText("Email"), { target: { value: " cy@x.io " } });
    fireEvent.change(within(dialog).getByLabelText("Role"), { target: { value: "editor" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Give" }));
    await waitFor(() =>
      expect(m(Namespaces.setCollectionMember).mock.calls[0][0]).toMatchObject({
        path: { name: "pods", cid: 3 },
        body: { email: "cy@x.io", role: "editor" },
      }),
    );
    fireEvent.change(within(list).getByLabelText("Role of Ann"), { target: { value: "admin" } });
    await waitFor(() =>
      expect(m(Namespaces.setCollectionMember).mock.calls[1][0].body).toEqual({ account: 7, role: "admin" }),
    );
    fireEvent.click(within(list).getByRole("button", { name: "Take away Ann’s role" }));
    await waitFor(() =>
      expect(m(Namespaces.setCollectionMember).mock.calls[2][0].body).toEqual({ account: 7, role: null }),
    );
  });
});

describe("moving recordings into a collection", () => {
  it("starts at the one they're in and moves them to another", async () => {
    m(Recordings.placeRecordings).mockImplementation(() => ok({ moved: 2 }));
    const onDone = jest.fn();
    const onOpenChange = jest.fn();
    wrap(
      <PlaceDialog
        ns="pods"
        ids={[7, 8]}
        title="2 recordings"
        current={2}
        open
        onOpenChange={onOpenChange}
        onDone={onDone}
      />,
    );
    const pick = await screen.findByLabelText("Collection");
    await waitFor(() => expect(pick).toHaveValue("2"));
    expect(screen.getByRole("button", { name: "Move" })).toHaveAttribute("aria-disabled", "true"); // already there
    fireEvent.change(pick, { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "Move" }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(m(Recordings.placeRecordings).mock.calls[0][0].body).toEqual({ recordings: [7, 8], collection: 3 });
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(toast).toHaveBeenCalledWith(expect.objectContaining({ title: "Moved 2 recordings to “2024”" }));
  });
});

describe("choosing where an import goes", () => {
  it("offers the namespace's collections, the default first chosen", async () => {
    const onChange = jest.fn();
    wrap(<CollectionField ns="pods" value={null} onChange={onChange} />);
    const pick = await screen.findByLabelText("Collection");
    await waitFor(() => expect(pick).toHaveValue("1"));
    expect(within(pick).getAllByRole("option")).toHaveLength(3);
    fireEvent.change(pick, { target: { value: "3" } });
    expect(onChange).toHaveBeenCalledWith(3);
  });

  it("says a new namespace starts with General, and waits for a namespace", () => {
    const { unmount } = wrap(<CollectionField ns="brand-new" value={null} onChange={() => {}} />);
    expect(screen.getByLabelText("Collection")).toBeDisabled();
    expect(screen.getByText("A new namespace starts with one collection, General.")).toBeInTheDocument();
    unmount();
    wrap(<CollectionField ns={null} value={null} onChange={() => {}} />);
    expect(screen.getByRole("option", { name: "Choose a namespace first" })).toBeInTheDocument();
    expect(Namespaces.listNamespaceCollections).not.toHaveBeenCalled();
  });
});
