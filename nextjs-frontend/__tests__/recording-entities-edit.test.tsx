import { fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";

import { useRec } from "@/components/recording/context";
import { EntitiesTab } from "@/components/recording/entities-tab";

jest.mock("@/components/recording/context", () => ({ useRec: jest.fn() }));
jest.mock("@/components/player/media", () => ({ usePlayerApi: () => ({ seek: jest.fn() }) }));
jest.mock("@/components/recording/hooks", () => ({
  useRecordingEntities: () => ({
    data: { items: [{ id: 7, name: "Acme", key: "acme", type: "ORG", aliases: ["Acme Inc"] }] },
    isLoading: false,
  }),
}));
jest.mock("@/components/topics/recording-topics", () => ({ RecordingTopics: () => null }));
jest.mock("@/components/recording/said-dates", () => ({ SaidDates: () => null }));
jest.mock("@/components/entities/entity-drawer", () => ({
  EntityDrawer: ({ id, ns, onClose }: { id: number; ns: string; onClose: () => void }) => (
    <div role="dialog" aria-label={`Entity ${id} in ${ns}`}>
      <button type="button" onClick={onClose}>
        Close
      </button>
    </div>
  ),
}));

const rec = (canEditNamespace: boolean) =>
  (useRec as jest.Mock).mockReturnValue({
    model: {
      entities: [
        { name: "Acme Inc", type: "ORG", segs: [0] },
        { name: "Mystery", type: "ORG", segs: [0] },
      ],
      keywords: [],
      segments: [{ t0: 0 }],
    },
    id: 1,
    entity: { selected: null, select: jest.fn() },
    where: () => "0:00",
    ns: "pods",
    canEditNamespace,
  });

describe("editing entities from a recording", () => {
  it("opens an entity the editor picks, without leaving the recording", () => {
    rec(true);
    render(<EntitiesTab />);
    // only entities the index knows can be edited
    expect(screen.queryByRole("button", { name: "Edit Mystery" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Edit Acme Inc" }));
    expect(screen.getByRole("dialog", { name: "Entity 7 in pods" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("offers viewers no pencil", () => {
    rec(false);
    render(<EntitiesTab />);
    expect(screen.queryByRole("button", { name: /^Edit / })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open Acme Inc in the graph" })).toBeInTheDocument();
  });
});
