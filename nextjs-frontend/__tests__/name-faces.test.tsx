import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom";
import type { ReactNode } from "react";

import { Video } from "@/app/openapi-client";
import type { UnnamedFace } from "@/app/openapi-client/types.gen";
import { NameFaces } from "@/components/speakers/name-faces";
import { ToastProvider } from "@/components/ui/toast";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Video: { getUnnamedFaces: jest.fn(), nameFace: jest.fn(), renameFace: jest.fn(), undoFaceMerge: jest.fn() },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
let role = "editor";
jest.mock("@/lib/hooks/session", () => ({
  useArchive: () => ({ can: (r: string) => r === "viewer" || role === "editor" }),
  needRole: (r: string, ns: string) => `Needs ${r} access to ${ns}`,
}));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const m = (f: unknown) => f as jest.Mock;

function face(id: number, over: Partial<UnnamedFace> = {}): UnnamedFace {
  return {
    id,
    label: `Face ${id}`,
    appearances: 1,
    screen_ms: 12_000,
    pages: 0,
    thumbnails: [{ recording: 7, cover_url: `/api/v1/recordings/7/frames/face-${id}.jpg?sig=x` }],
    recordings: [{ id: 7, title: "Weekly review" }],
    ...over,
  };
}

let groups: UnnamedFace[] = [];
let mode = "recognize";
function serve() {
  m(Video.getUnnamedFaces).mockImplementation(() => ok({ mode, total: groups.length, groups }));
  m(Video.nameFace).mockImplementation(({ path, body }: { path: { fid: number }; body: { name: string } }) => {
    groups = groups.filter((g) => g.id !== path.fid);
    return ok({
      ok: true,
      name: body.name,
      into: body.name === "Mara" ? 9 : null,
      merge: body.name === "Mara" ? 31 : null,
    });
  });
  m(Video.renameFace).mockImplementation(() => ok({ ok: true }));
  m(Video.undoFaceMerge).mockImplementation(() => ok({ ok: true }));
}

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
  role = "editor";
  mode = "recognize";
  groups = [
    face(3, {
      appearances: 2,
      thumbnails: [
        { recording: 7, cover_url: "/a.jpg?sig=x" },
        { recording: 8, cover_url: "/b.jpg?sig=x" },
      ],
      recordings: [
        { id: 7, title: "Weekly review" },
        { id: 8, title: "Board deck" },
      ],
    }),
    face(5, { guess: "Mara", screen_ms: 0, pages: 2 }),
    face(6),
  ];
  serve();
});

describe("naming faces", () => {
  it("focuses the largest group first; Enter names it and moves on to the next, with Undo", async () => {
    wrap(<NameFaces ns="pods" />);
    const first = await screen.findByRole("textbox", { name: "Name for Face 3" });
    await waitFor(() => expect(first).toHaveFocus());
    const row = screen.getByRole("listitem", { name: "Face 3" });
    expect(within(row).getByText("2 assets · 12 s on screen")).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "Board deck" })).toHaveAttribute("href", "/resources/8");
    expect(row.querySelectorAll("img")).toHaveLength(2);
    expect(screen.getByText(/3 faces without a name/)).toBeInTheDocument();

    fireEvent.change(first, { target: { value: "Ann" } });
    fireEvent.submit(first.closest("form")!);
    await waitFor(() =>
      expect(Video.nameFace).toHaveBeenCalledWith(expect.objectContaining({ path: { fid: 3 }, body: { name: "Ann" } })),
    );
    const next = await screen.findByRole("textbox", { name: "Name for Face 5" });
    await waitFor(() => expect(next).toHaveFocus()); // focus moves on to the next one
    expect(next).toHaveValue("Mara"); // the name Lens offers, ready for Enter
    expect(screen.getByRole("listitem", { name: "Face 5" })).toHaveTextContent("1 asset · 2 pages");
    expect(screen.queryByRole("listitem", { name: "Face 3" })).not.toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: "Undo" })); // Face 3 is Ann: undo renames it back
    await waitFor(() =>
      expect(Video.renameFace).toHaveBeenCalledWith(expect.objectContaining({ path: { fid: 3 }, body: { name: "" } })),
    );
  });

  it("joins a face to the person who has the name, and Undo takes the merge back", async () => {
    groups = groups.slice(1);
    wrap(<NameFaces ns="pods" />);
    const input = await screen.findByRole("textbox", { name: "Name for Face 5" });
    fireEvent.submit(input.closest("form")!);
    expect(await screen.findByText("Face 5 joined Mara")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() =>
      expect(Video.undoFaceMerge).toHaveBeenCalledWith(expect.objectContaining({ path: { mid: 31 } })),
    );
  });

  it("says why when faces aren't recognised, nobody is waiting, or the person can't name them", async () => {
    mode = "detect";
    groups = [];
    const { unmount } = wrap(<NameFaces ns="pods" />);
    expect(await screen.findByText("Faces aren’t recognised in pods")).toBeInTheDocument();
    unmount();
    mode = "recognize";
    const empty = wrap(<NameFaces ns="pods" />);
    expect(await screen.findByText("Every face has a name")).toBeInTheDocument();
    empty.unmount();
    role = "viewer";
    groups = [face(6)];
    wrap(<NameFaces ns="pods" />);
    expect(await screen.findByRole("textbox", { name: "Name for Face 6" })).toBeDisabled();
  });
});
