import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Notes } from "@/app/openapi-client";
import { useDeleteNote } from "@/components/notes/delete";
import { RecentlyDeleted } from "@/components/notes/recently-deleted";

jest.mock("@/app/openapi-client", () => ({
  Notes: { deletePage: jest.fn(), restoreDeleted: jest.fn(), deletedPages: jest.fn(), purgeDeleted: jest.fn() },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
const push = jest.fn();
jest.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
const toast = jest.fn();
jest.mock("@/components/ui/toast", () => ({ useToast: () => toast }));
jest.mock("@/lib/hooks/session", () => ({ useArchive: () => ({ can: () => true }) }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const m = (f: unknown) => f as jest.Mock;
const show = (ui: React.ReactNode) =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      {ui}
    </QueryClientProvider>,
  );

beforeEach(() => jest.clearAllMocks());

function DeleteButton() {
  const del = useDeleteNote();
  return <button onClick={() => void del(7, "Plans")}>Delete</button>;
}

describe("deleting a note", () => {
  it("deletes at once and the notice's Undo puts it back and opens it", async () => {
    m(Notes.deletePage).mockImplementation(() => ok({ ok: true }));
    m(Notes.restoreDeleted).mockImplementation(() => ok({ id: 7, title: "Plans" }));
    show(<DeleteButton />);
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(toast).toHaveBeenCalled());
    expect(m(Notes.deletePage).mock.calls[0][0].path).toEqual({ pid: 7 });
    const notice = toast.mock.calls[0][0];
    expect(notice.title).toBe("Deleted “Plans”");
    expect(notice.action.label).toBe("Undo");
    notice.action.onClick();
    await waitFor(() => expect(push).toHaveBeenCalledWith("/notes/7"));
    expect(m(Notes.restoreDeleted).mock.calls[0][0].path).toEqual({ pid: 7 });
  });

  it("opens a thing's page where it belongs after Undo", async () => {
    m(Notes.deletePage).mockImplementation(() => ok({ ok: true }));
    m(Notes.restoreDeleted).mockImplementation(() => ok({ id: 7, title: "Ada", about: "entity:5" }));
    show(<DeleteButton />);
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(toast).toHaveBeenCalled());
    toast.mock.calls[0][0].action.onClick();
    await waitFor(() => expect(push).toHaveBeenCalledWith("/notes/about/entity/5"));
  });
});

describe("recently deleted", () => {
  const gone = [
    { id: 3, title: "Old plans", namespace: "home", deleted_at: new Date().toISOString(), deleted_by: "ed@x.io" },
  ];

  it("puts a note back with one click", async () => {
    m(Notes.deletedPages).mockImplementation(() => ok(gone));
    m(Notes.restoreDeleted).mockImplementation(() => ok({ id: 3, title: "Old plans" }));
    show(<RecentlyDeleted ns="home" />);
    expect(await screen.findByText("Old plans")).toBeInTheDocument();
    expect(screen.getByText(/by ed@x\.io/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Put back" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/notes/3"));
  });

  it("deletes one for good only on a second click", async () => {
    m(Notes.deletedPages).mockImplementation(() => ok(gone));
    m(Notes.purgeDeleted).mockImplementation(() => ok({ ok: true }));
    show(<RecentlyDeleted ns="home" />);
    fireEvent.click(await screen.findByRole("button", { name: "Delete now" }));
    expect(Notes.purgeDeleted).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Delete for good" }));
    await waitFor(() => expect(Notes.purgeDeleted).toHaveBeenCalled());
    expect(m(Notes.purgeDeleted).mock.calls[0][0].path).toEqual({ pid: 3 });
  });

  it("shows nothing while nothing was deleted", async () => {
    m(Notes.deletedPages).mockImplementation(() => ok([]));
    const { container } = show(<RecentlyDeleted ns="home" />);
    await waitFor(() => expect(Notes.deletedPages).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
