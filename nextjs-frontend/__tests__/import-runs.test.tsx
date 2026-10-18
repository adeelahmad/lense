import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";
import type { ReactNode } from "react";

import { Imports } from "@/app/openapi-client";
import { canUndoRun, runDetail, runTitle, undoneToast } from "@/components/import/import-runs";
import { RecentImports } from "@/components/import/recent-imports";
import { WebLinks } from "@/components/import/web-links";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Imports: { listImportRuns: jest.fn(), undoImportRun: jest.fn(), importWebLinks: jest.fn() },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
const toast = jest.fn();
jest.mock("@/components/ui/toast", () => ({ useToast: () => toast }));
const m = (f: unknown) => f as jest.Mock;
const ok = (data: unknown) => ({ data, response: { ok: true, status: 200 } });

const RUN = {
  id: 7,
  kind: "archive" as const,
  namespace: "pods",
  source: "export.zip",
  created_by: "own@x.io",
  created_at: new Date().toISOString(),
  assets: 240,
  left: 240,
  status: "imported" as const,
};

function wrap(children: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>{children}</TooltipProvider>
    </QueryClientProvider>,
  );
}

beforeAll(() => {
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});
beforeEach(() => jest.clearAllMocks());

describe("import runs", () => {
  it("says what a run made, what's left and what an undo did", () => {
    expect(runTitle(RUN)).toBe("Imported 240 assets");
    expect(runTitle({ ...RUN, left: 3, status: "partly undone" })).toBe("Imported 240 assets, 3 left");
    expect(runTitle({ ...RUN, left: 0, status: "undone" })).toBe("Undone: 240 assets removed");
    expect(runDetail(RUN)).toBe("Archive · export.zip · into pods");
    expect(runDetail({ kind: "scan", source: null, namespace: null })).toBe("Folder scan");
    expect(canUndoRun(RUN)).toBe(true);
    expect(canUndoRun({ left: 0 })).toBe(false);
    expect(undoneToast({ deleted: [1, 2], busy: [] })).toEqual({ title: "Removed 2 assets" });
    expect(undoneToast({ deleted: [1], busy: [5] })).toEqual({
      title: "Removed 1 asset",
      body: "1 asset is being processed: undo the import again once it stops.",
    });
  });

  it("lists recent runs and undoes one whole, asking once in the row", async () => {
    m(Imports.listImportRuns).mockResolvedValue(ok([RUN, { ...RUN, id: 6, left: 0, status: "undone" }]));
    m(Imports.undoImportRun).mockResolvedValue(
      ok({ run: { ...RUN, left: 0, status: "undone" }, deleted: Array.from({ length: 240 }, (_, i) => i), busy: [] }),
    );
    wrap(<RecentImports />);
    expect(await screen.findByText("Imported 240 assets")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Undo" })).toHaveLength(1); // the undone run has nothing left
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    fireEvent.click(screen.getByRole("button", { name: "Keep" }));
    expect(Imports.undoImportRun).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete 240 assets" }));
    await waitFor(() => expect(Imports.undoImportRun).toHaveBeenCalled());
    expect(m(Imports.undoImportRun).mock.calls[0][0].path).toEqual({ run_id: 7 });
    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(expect.objectContaining({ title: "Removed 240 assets", tone: "green" })),
    );
  });

  it("offers Undo on the toast after a bulk import, which undoes the whole run", async () => {
    m(Imports.importWebLinks).mockResolvedValue(
      ok({
        results: [
          { url: "https://example.org/a", status: "queued", recording: 1, job: 1, import_run: 9 },
          { url: "https://example.org/b", status: "queued", recording: 2, job: 2, import_run: 9 },
        ],
        import_run: 9,
      }),
    );
    m(Imports.undoImportRun).mockResolvedValue(ok({ run: { ...RUN, id: 9 }, deleted: [1, 2], busy: [] }));
    wrap(<WebLinks namespace="pods" blockReason={null} pipeline={null} collection={null} />);
    fireEvent.change(screen.getByLabelText(/Links/), {
      target: { value: "https://example.org/a\nhttps://example.org/b" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Capture them all" }));
    await waitFor(() => expect(toast).toHaveBeenCalled());
    const shown = toast.mock.calls[0][0];
    expect(shown).toEqual(expect.objectContaining({ title: "Capturing 2 pages", action: expect.anything() }));
    expect(shown.action.label).toBe("Undo");
    shown.action.onClick();
    await waitFor(() => expect(Imports.undoImportRun).toHaveBeenCalled());
    expect(m(Imports.undoImportRun).mock.calls[0][0].path).toEqual({ run_id: 9 });
    await waitFor(() => expect(toast).toHaveBeenCalledWith(expect.objectContaining({ title: "Removed 2 assets" })));
  });
});
