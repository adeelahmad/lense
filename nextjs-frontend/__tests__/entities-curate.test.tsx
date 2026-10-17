import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Entities } from "@/app/openapi-client";
import { EntityBulkBar } from "@/components/entities/bulk-bar";
import { keepOf, MaybeSame, MergeInto, mergeTitle } from "@/components/entities/curate";
import type { EntityRow } from "@/components/entities/model";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Entities: {
    listEntities: jest.fn(),
    mergeEntities: jest.fn(),
    undoEntityMerge: jest.fn(),
    listEntitySuggestions: jest.fn(),
    markEntitiesNotSame: jest.fn(),
  },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
const toast = jest.fn();
jest.mock("@/components/ui/toast", () => ({ useToast: () => toast }));

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

const row = (id: number, name: string, mentions: number, extra: Partial<EntityRow> = {}): EntityRow => ({
  id,
  name,
  type: "ORG",
  type_label: "Organisation",
  key: name.toLowerCase(),
  aliases: [],
  mentions,
  recordings: 1,
  hidden: false,
  ...extra,
});

beforeEach(() => jest.clearAllMocks());

describe("which entity a merge keeps", () => {
  it("keeps the one on the fixed list, else the most mentioned, else the oldest", () => {
    expect(keepOf([row(3, "NWL", 2), row(1, "Northwind Labs", 9)])?.id).toBe(1);
    expect(keepOf([row(3, "NWL", 2, { defined: true }), row(1, "Northwind Labs", 9)])?.id).toBe(3);
    expect(keepOf([row(5, "B", 4), row(2, "A", 4)])?.id).toBe(2);
    expect(keepOf([])).toBeUndefined();
  });

  it("says what was merged into what", () => {
    expect(mergeTitle("Acme", ["Acme Inc"])).toBe("Merged Acme Inc into Acme");
    expect(mergeTitle("Acme", ["Acme Inc", "ACME", "Acme Co"])).toBe("Merged 3 entities into Acme");
  });
});

describe("merging into an entity", () => {
  it("finds one by typing and merges it at once, with Undo on the toast", async () => {
    m(Entities.listEntities).mockReturnValue(
      ok({
        total: 3,
        items: [
          { id: 1, name: "Northwind Labs", type_label: "Organisation", mentions: 9 },
          { id: 2, name: "North Wind Labs", type_label: "Organisation", mentions: 2 },
          { id: 7, name: "Unknown", builtin: "unknown", mentions: 0 },
        ],
      }),
    );
    m(Entities.mergeEntities).mockReturnValue(ok({ ok: true, merge: 44 }));
    m(Entities.undoEntityMerge).mockReturnValue(ok({ ok: true }));
    show(<MergeInto keep={{ id: 1, name: "Northwind Labs" }} ns="pods" open onOpenChange={() => undefined} />);
    const box = screen.getByRole("combobox", { name: "Entity to merge into Northwind Labs" });
    expect(box).toHaveFocus();
    fireEvent.change(box, { target: { value: "north" } });
    // the list for what was typed, not the first one (which empties while the typed one loads, on a slow runner)
    await waitFor(() => expect(m(Entities.listEntities).mock.calls.some(([o]) => o.query.q === "north")).toBe(true));
    // itself and the entities that are always there can't be merged
    await waitFor(() => expect(screen.getAllByRole("option")).toHaveLength(1));
    expect(screen.getByRole("option")).toHaveTextContent("North Wind Labs");
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(Entities.mergeEntities).toHaveBeenCalled());
    expect(m(Entities.mergeEntities).mock.calls[0][0].body).toEqual({ keep: 1, others: [2] });
    await waitFor(() => expect(toast).toHaveBeenCalled());
    const t = toast.mock.calls[0][0];
    expect(t.title).toBe("Merged North Wind Labs into Northwind Labs");
    t.action.onClick();
    await waitFor(() => expect(Entities.undoEntityMerge).toHaveBeenCalled());
    expect(m(Entities.undoEntityMerge).mock.calls[0][0].path).toEqual({ mid: 44 });
  });

  it("waits for the list of what was typed when Enter comes first", async () => {
    const all = [
      { id: 2, name: "Matterbridge", type_label: "Term", mentions: 5 },
      { id: 3, name: "Lens", type_label: "Term", mentions: 2 },
    ];
    m(Entities.listEntities).mockImplementation(({ query }: { query: { q?: string } }) =>
      ok({ total: 2, items: query.q ? all.filter((e) => e.name.toLowerCase().includes(query.q!)) : all }),
    );
    m(Entities.mergeEntities).mockReturnValue(ok({ ok: true, merge: 45 }));
    show(<MergeInto keep={{ id: 1, name: "Raspberry Pi" }} ns="pods" open onOpenChange={() => undefined} />);
    const box = screen.getByRole("combobox", { name: "Entity to merge into Raspberry Pi" });
    await waitFor(() => expect(screen.getAllByRole("option")).toHaveLength(2));
    // typed and Enter at once: the unfiltered list (Matterbridge first) is still showing
    fireEvent.change(box, { target: { value: "lens" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(Entities.mergeEntities).toHaveBeenCalled());
    expect(m(Entities.mergeEntities).mock.calls[0][0].body).toEqual({ keep: 1, others: [3] });
    expect(Entities.mergeEntities).toHaveBeenCalledTimes(1);
  });

  it("offers the ones that may be the same, to merge or set apart", async () => {
    m(Entities.listEntitySuggestions).mockReturnValue(
      ok([
        {
          reason: "acronym",
          confidence: 0.85,
          a: { id: 4, name: "AWS", mentions: 3 },
          b: { id: 5, name: "Amazon Web Services", mentions: 1 },
        },
      ]),
    );
    m(Entities.markEntitiesNotSame).mockReturnValue(ok({ ok: true }));
    m(Entities.mergeEntities).mockReturnValue(ok({ ok: true, merge: 3 }));
    show(<MaybeSame entity={{ id: 5, name: "Amazon Web Services" }} canEdit />);
    expect(await screen.findByText("AWS")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Not the same" }));
    await waitFor(() => expect(Entities.markEntitiesNotSame).toHaveBeenCalled());
    expect(m(Entities.markEntitiesNotSame).mock.calls[0][0].body).toEqual({ a: 5, b: 4 });
    fireEvent.click(screen.getByRole("button", { name: "Merge" }));
    await waitFor(() => expect(Entities.mergeEntities).toHaveBeenCalled());
    expect(m(Entities.mergeEntities).mock.calls[0][0].body).toEqual({ keep: 5, others: [4] });
  });

  it("shows nothing to viewers", () => {
    show(<MaybeSame entity={{ id: 5, name: "Amazon Web Services" }} canEdit={false} />);
    expect(Entities.listEntitySuggestions).not.toHaveBeenCalled();
  });
});

describe("the bulk bar", () => {
  it("merges the picked into the one it guesses, in one click", () => {
    const onMerge = jest.fn();
    show(
      <EntityBulkBar
        picked={[row(3, "NWL", 2), row(1, "Northwind Labs", 9)]}
        types={[{ type: "ORG", label: "Organisation" } as never]}
        busy={false}
        onMerge={onMerge}
        onRetype={() => undefined}
        onClear={() => undefined}
      />,
    );
    expect(screen.getByRole("toolbar", { name: "2 entities selected" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Merge into Northwind Labs/ }));
    expect(onMerge.mock.calls[0][0].id).toBe(1);
  });

  it("needs two to merge", () => {
    show(
      <EntityBulkBar
        picked={[row(3, "NWL", 2)]}
        types={[]}
        busy={false}
        onMerge={() => undefined}
        onRetype={() => undefined}
        onClear={() => undefined}
      />,
    );
    expect(screen.getByRole("button", { name: "Merge" })).toBeDisabled();
  });
});
