import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom";
import { useMemo, useState } from "react";

import { Resources } from "@/app/openapi-client";
import { PlayerProvider } from "@/components/player/media";
import { RecordingProvider, type PanelTab, type RecordingCtx } from "@/components/recording/context";
import { DocumentLayout } from "@/components/recording/document/document-layout";
import { pageAt, pageRef } from "@/components/recording/document/model";
import { findInSegments, type PageInfo, type PlayerModel, type Segment } from "@/components/recording/model";
import { TooltipProvider } from "@/components/ui/tooltip";

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
jest.mock("@/app/openapi-client", () => ({
  Notes: { listNotes: jest.fn(() => Promise.resolve({ data: [], response: { ok: true, status: 200 } })) },
  Resources: { editSegment: jest.fn() },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
const toast = jest.fn();
jest.mock("@/components/ui/toast", () => ({ useToast: () => toast }));
// the header and the other panels have tests of their own
jest.mock("@/components/recording/header", () => ({
  RecordingHeader: () => <header>Harbour report</header>,
  HeaderActions: () => null,
  Banners: () => null,
}));
jest.mock("@/components/recording/side-panel", () => ({
  MORE_TABS: [{ value: "files", label: "Files" }],
  PanelTabs: ({
    tabs,
    value,
    onChange,
  }: {
    tabs: { value: string; label: string }[];
    value: string;
    onChange: (v: string) => void;
  }) => (
    <div role="tablist">
      {tabs.map((t) => (
        <button
          key={t.value}
          type="button"
          role="tab"
          aria-selected={t.value === value}
          onClick={() => onChange(t.value)}
        >
          {t.label}
        </button>
      ))}
    </div>
  ),
  PanelScroll: ({ children }: { children: React.ReactNode }) => <div role="tabpanel">{children}</div>,
  PanelBody: ({ tab }: { tab: string }) => <p>The {tab} panel</p>,
}));

const page = (idx: number, over: Partial<PageInfo> = {}): PageInfo => ({
  idx,
  width: 1545,
  height: 2000,
  image: `/api/v1/recordings/9/frames/page-000${idx + 1}.jpg?sig=x`,
  thumb: `/api/v1/recordings/9/frames/thumb-000${idx + 1}.jpg?sig=x`,
  text: "pdf",
  chars: 60,
  label: null,
  ...over,
});
const block = (idx: number, t0: number, p: number, text: string): Segment => ({
  idx,
  t0,
  t1: t0 + 1000,
  speaker: null,
  text,
  emotion: null,
  event: null,
  page: p,
  box: [0.1, 0.1 + idx * 0.1, 0.6, 0.05],
});
const MODEL = {
  id: 9,
  title: "Harbour report",
  segments: [
    block(0, 0, 0, "The harbour report"),
    block(1, 1000, 1, "Second page about the lighthouse keeper."),
    block(2, 2000, 2, "A note in the margin."),
  ],
  pages: [page(0), page(1, { text: "ocr" }), page(2, { label: "iii" })],
  media: { kind: "document", pages: 3, width: 1545, height: 2000, fps: null },
} as unknown as PlayerModel;

function Harness({ model, canEdit, startPage }: { model: PlayerModel; canEdit: boolean; startPage: number | null }) {
  const [tab, setTab] = useState<PanelTab>("pages");
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const [editing, setEditing] = useState(false);
  const hits = useMemo(() => findInSegments(model.segments, query), [model.segments, query]);
  const ctx = {
    id: 9,
    ns: "pods",
    model,
    rec: { id: 9, title: "Harbour report", collection_path: [] },
    state: { phase: "ready", job: null },
    jobs: [],
    tab,
    setTab,
    find: { open: true, query, hits, index, setOpen: () => {}, setQuery, setIndex },
    canEdit,
    editing,
    setEditing,
    paged: true,
    startPage,
    where: (ms: number) => pageRef(model.pages, pageAt(model.segments, ms)),
  } as unknown as RecordingCtx;
  return (
    <RecordingProvider value={ctx}>
      <DocumentLayout compact={false} />
    </RecordingProvider>
  );
}

function show(
  { canEdit = true, startPage = null, model = MODEL } = {} as {
    canEdit?: boolean;
    startPage?: number | null;
    model?: PlayerModel;
  },
) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <PlayerProvider hasMedia={false} durationMs={3000}>
          <Harness model={model} canEdit={canEdit} startPage={startPage} />
        </PlayerProvider>
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

const shown = () => screen.getByRole("img", { name: /^Page / });

beforeAll(() => {
  Element.prototype.scrollIntoView = jest.fn();
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
  // a wide screen: the thumbnails show
  window.matchMedia = ((q: string) => ({
    matches: q.includes("min-width"),
    addEventListener: () => {},
    removeEventListener: () => {},
  })) as unknown as typeof window.matchMedia;
});
beforeEach(() => jest.clearAllMocks());

describe("a document's page", () => {
  it("shows its pages beside their text, and turns them", () => {
    show();
    const thumbs = screen.getByRole("navigation", { name: "Pages to choose" });
    expect(
      within(thumbs)
        .getAllByRole("button")
        .map((b) => b.getAttribute("aria-label")),
    ).toEqual(["Page 1", "Page 2", "Page iii"]);
    expect(shown()).toHaveAttribute("alt", "Page 1");
    expect(screen.getByRole("textbox", { name: "Page, of 3" })).toHaveValue("1");
    expect(screen.getByText("Text from the PDF", { selector: "span.truncate" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(shown()).toHaveAttribute("alt", "Page 2");
    expect(within(thumbs).getByRole("button", { name: "Page 2" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByText("Read by OCR", { selector: "span.truncate" })).toBeInTheDocument();

    fireEvent.click(within(thumbs).getByRole("button", { name: "Page iii" }));
    expect(shown()).toHaveAttribute("alt", "Page iii");
    fireEvent.keyDown(window, { key: "ArrowLeft" });
    expect(shown()).toHaveAttribute("alt", "Page 2");

    const field = screen.getByRole("textbox", { name: "Page, of 3" });
    fireEvent.change(field, { target: { value: "1" } });
    fireEvent.keyDown(field, { key: "Enter" });
    expect(shown()).toHaveAttribute("alt", "Page 1");

    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(screen.getByRole("button", { name: "Zoom 125%: fit the width" })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "-" });
    fireEvent.keyDown(window, { key: "-" });
    expect(screen.getByRole("button", { name: "Zoom 75%: fit the width" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Zoom 75%: fit the width" }));
    expect(screen.getByRole("button", { name: "Fits the width" })).toBeInTheDocument();

    // the other panels are a tab away
    fireEvent.click(screen.getByRole("tab", { name: "Summary" }));
    expect(screen.getByText("The summary panel")).toBeInTheDocument();
  });

  it("marks a chosen block on its page, and turns to each find match", async () => {
    const { container } = show();
    fireEvent.click(screen.getByText("Second page about the lighthouse keeper."));
    expect(shown()).toHaveAttribute("alt", "Page 2");
    expect(screen.getByText("Second page about the lighthouse keeper.").closest("[data-block]")).toHaveAttribute(
      "aria-current",
      "true",
    );
    expect(container.querySelector("[data-mark=selected]")).toHaveStyle({ top: "20%", width: "60%" });

    fireEvent.change(screen.getByRole("searchbox", { name: "Find in the text" }), { target: { value: "harbour" } });
    await waitFor(() => expect(shown()).toHaveAttribute("alt", "Page 1"));
    expect(screen.getByRole("status")).toHaveTextContent("1 of 1");
    expect(container.querySelector("[data-mark=current-hit]")).toBeInTheDocument();
    expect(container.querySelector("mark[data-hit=current]")).toHaveTextContent("harbour");
  });

  it("lets editors correct a block's text, and tells viewers why they can't", async () => {
    const view = show({ canEdit: false });
    expect(screen.getByRole("button", { name: "Correct text" })).toHaveAttribute("aria-disabled", "true");
    view.unmount();

    (Resources.editSegment as jest.Mock).mockImplementation(() => ok({ ok: true }));
    show();
    fireEvent.click(screen.getByRole("button", { name: "Correct text" }));
    fireEvent.click(screen.getByRole("button", { name: /^Correct: Second page/ }));
    const box = screen.getByRole("textbox", { name: "The block's text" });
    fireEvent.change(box, { target: { value: "Second page about the lighthouse keepers." } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
    });
    await waitFor(() =>
      expect((Resources.editSegment as jest.Mock).mock.calls[0][0]).toMatchObject({
        path: { rid: 9, idx: 1 },
        body: { text: "Second page about the lighthouse keepers." },
      }),
    );
    await waitFor(() => expect(screen.queryByRole("textbox", { name: "The block's text" })).not.toBeInTheDocument());
  });

  it("opens on the page asked for", () => {
    show({ startPage: 1 });
    expect(shown()).toHaveAttribute("alt", "Page 2");
    expect(screen.getByRole("textbox", { name: "Page, of 3" })).toHaveValue("2");
  });

  it("says when a page couldn't be drawn", () => {
    show({
      model: { ...MODEL, pages: [page(0, { image: null, thumb: null, width: null, height: null })] } as PlayerModel,
    });
    expect(screen.queryByRole("img", { name: /^Page / })).not.toBeInTheDocument();
    expect(screen.getByText(/couldn't be drawn on the server/)).toBeInTheDocument();
  });
});
