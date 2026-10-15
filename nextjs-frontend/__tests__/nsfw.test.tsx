import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Namespaces, Resources } from "@/app/openapi-client";
import type { NsfwState } from "@/app/openapi-client/types.gen";
import { NsfwNotice, NsfwPictures, RevealCover, useBlur } from "@/components/nsfw/nsfw";
import { nsfwText, ownSwitch, parseThreshold, switchValue } from "@/components/nsfw/nsfw-model";
import { NsfwSection } from "@/components/nsfw/nsfw-section";
import { RecordingProvider, type RecordingCtx } from "@/components/recording/context";
import { libraryQuery, NO_FILTERS } from "@/components/library/model";
import { reprocessOptions } from "@/components/recording/jobs";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Namespaces: {
    getNamespaceNsfw: jest.fn(),
    updateNamespaceNsfw: jest.fn(),
    classifyNamespaceNsfw: jest.fn(),
  },
  Resources: { markRecordingNsfw: jest.fn() },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
let editor = true;
jest.mock("@/lib/hooks/session", () => ({
  ...jest.requireActual("@/lib/hooks/session"),
  useArchive: () => ({ can: () => editor }),
}));
const toast = jest.fn();
jest.mock("@/components/ui/toast", () => ({ useToast: () => toast }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const m = (f: unknown) => f as jest.Mock;
const wrap = (ui: React.ReactNode) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>{ui}</TooltipProvider>
    </QueryClientProvider>,
  );
};

const FLAGGED: NsfwState = {
  flagged: true,
  score: 0.93,
  manual: null,
  blur: true,
  display: "blur",
  threshold: 0.8,
  checked: 12,
  of: 300,
};

beforeEach(() => {
  jest.clearAllMocks();
  editor = true;
});

describe("what the web app says", () => {
  it("describes what classification found", () => {
    expect(nsfwText(FLAGGED)).toBe("Flagged NSFW: highest score 0.93 (threshold 0.80), 12 of 300 frames scored");
    expect(nsfwText({ ...FLAGGED, flagged: false, score: 0.04, checked: 1, of: 1 }, true)).toBe(
      "Not NSFW: highest score 0.04 (threshold 0.80)",
    );
    expect(nsfwText({ ...FLAGGED, manual: true, score: null })).toBe("Marked NSFW by hand");
    expect(nsfwText({ ...FLAGGED, flagged: false, manual: false, checked: 2, of: 2 }, true)).toBe(
      "Marked not NSFW by hand; the model's highest score 0.93 (threshold 0.80), 2 of 2 pages scored",
    );
  });

  it("reads a namespace's own choices and a typed threshold", () => {
    expect([ownSwitch({}), ownSwitch({ enabled: true }), ownSwitch({ enabled: false })]).toEqual([
      "archive",
      "on",
      "off",
    ]);
    expect([switchValue("archive"), switchValue("on"), switchValue("off")]).toEqual([null, true, false]);
    expect([parseThreshold(""), parseThreshold(" 0.85 "), parseThreshold("0.4"), parseThreshold("x")]).toEqual([
      null,
      0.85,
      undefined,
      undefined,
    ]);
  });

  it("filters the Library and offers the step where there are pictures", () => {
    const byDate = { key: "date" as const, dir: "desc" as const };
    expect(libraryQuery({ ...NO_FILTERS, sensitive: "exclude" }, "all", byDate, null)).toEqual({
      sort: "-date",
      sensitive: "exclude",
    });
    expect(libraryQuery(NO_FILTERS, "all", byDate, null)).not.toHaveProperty("sensitive");
    const keys = (o: Parameters<typeof reprocessOptions>[0]) => reprocessOptions(o).map((x) => x.key);
    expect(keys({ video: true, hasAudio: true })).toContain("nsfw");
    expect(keys({ video: false, hasAudio: false, paged: true })).toContain("nsfw");
    expect(keys({ video: false, hasAudio: true })).not.toContain("nsfw");
  });
});

function Picture() {
  const blur = useBlur();
  return (
    <div className="relative">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img alt="page 1" src="/p.png" className={blur} />
      <RevealCover />
    </div>
  );
}

const ctx = (nsfw: NsfwState | null, canEdit = true) =>
  ({ id: 9, ns: "pods", rec: { id: 9, title: "Beach", nsfw }, paged: false, canEdit }) as unknown as RecordingCtx;

describe("a flagged resource's page", () => {
  it("blurs its pictures until someone shows them, and blurs them again", () => {
    wrap(
      <RecordingProvider value={ctx(FLAGGED)}>
        <NsfwPictures state={FLAGGED}>
          <NsfwNotice />
          <Picture />
        </NsfwPictures>
      </RecordingProvider>,
    );
    expect(screen.getByText(/Flagged NSFW: highest score 0.93/)).toBeInTheDocument();
    expect(screen.getByAltText("page 1")).toHaveClass("blur-xl");
    fireEvent.click(screen.getByRole("button", { name: "Show NSFW pictures" }));
    expect(screen.getByAltText("page 1")).not.toHaveClass("blur-xl");
    expect(screen.queryByRole("button", { name: "Show NSFW pictures" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Blur them" }));
    expect(screen.getByAltText("page 1")).toHaveClass("blur-xl");
  });

  it("shows pictures as they are where the namespace shows flagged ones, or nothing was flagged", () => {
    const shown = { ...FLAGGED, blur: false, display: "show" as const };
    wrap(
      <RecordingProvider value={ctx(shown)}>
        <NsfwPictures state={shown}>
          <NsfwNotice />
          <Picture />
        </NsfwPictures>
      </RecordingProvider>,
    );
    expect(screen.getByAltText("page 1")).not.toHaveClass("blur-xl");
    expect(screen.getByText(/Flagged NSFW/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Show pictures" })).not.toBeInTheDocument();
  });

  it("editors mark it not NSFW, with Undo", async () => {
    m(Resources.markRecordingNsfw).mockReturnValue(ok({ ...FLAGGED, flagged: false, manual: false, blur: false }));
    wrap(
      <RecordingProvider value={ctx(FLAGGED)}>
        <NsfwPictures state={FLAGGED}>
          <NsfwNotice />
        </NsfwPictures>
      </RecordingProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Not NSFW" }));
    await waitFor(() =>
      expect(Resources.markRecordingNsfw).toHaveBeenCalledWith(
        expect.objectContaining({ path: { rid: 9 }, body: { flagged: false } }),
      ),
    );
    await waitFor(() => expect(toast).toHaveBeenCalled());
    const t = toast.mock.calls[0][0];
    expect(t.title).toBe("Marked not NSFW");
    t.action.onClick();
    await waitFor(() =>
      expect(Resources.markRecordingNsfw).toHaveBeenLastCalledWith(
        expect.objectContaining({ body: { flagged: null } }),
      ),
    );
  });

  it("viewers can't mark it", () => {
    wrap(
      <RecordingProvider value={ctx(FLAGGED, false)}>
        <NsfwNotice />
      </RecordingProvider>,
    );
    expect(screen.getByRole("button", { name: "Not NSFW" })).toHaveAttribute("aria-disabled", "true");
  });
});

const STATUS = {
  enabled: false,
  threshold: 0.8,
  display: "blur",
  own: { enabled: null, threshold: null, display: null },
  archive: { enabled: false, threshold: 0.8, display: "blur" },
  classified: 0,
  flagged: 0,
  unclassified: 4,
};

describe("a namespace's NSFW section", () => {
  it("owners turn it on, set the threshold, and editors classify what was there before", async () => {
    m(Namespaces.getNamespaceNsfw).mockReturnValue(ok(STATUS));
    const on = { ...STATUS, enabled: true, own: { ...STATUS.own, enabled: true } };
    m(Namespaces.updateNamespaceNsfw).mockReturnValue(ok(on));
    m(Namespaces.classifyNamespaceNsfw).mockReturnValue(ok({ ok: true, jobs: [1, 2, 3, 4] }));
    wrap(<NsfwSection ns="pods" isOwner />);
    fireEvent.click(await screen.findByRole("radio", { name: /^On/ }));
    await waitFor(() =>
      expect(Namespaces.updateNamespaceNsfw).toHaveBeenCalledWith(
        expect.objectContaining({ path: { name: "pods" }, body: { enabled: true } }),
      ),
    );
    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(expect.objectContaining({ title: "NSFW classification is on in pods" })),
    );
    // Undo puts back what the namespace had chosen
    toast.mock.calls[0][0].action.onClick();
    await waitFor(() =>
      expect(Namespaces.updateNamespaceNsfw).toHaveBeenLastCalledWith(
        expect.objectContaining({ body: { enabled: null, threshold: null, display: null } }),
      ),
    );

    m(Namespaces.updateNamespaceNsfw).mockReturnValue(
      ok({ ...on, threshold: 0.9, own: { ...on.own, threshold: 0.9 } }),
    );
    const box = screen.getByLabelText("Flag at score");
    fireEvent.change(box, { target: { value: "0.3" } });
    expect(screen.getByText("A score from 0.5 to 0.99, or empty")).toBeInTheDocument();
    fireEvent.change(box, { target: { value: "0.9" } });
    fireEvent.submit(box);
    await waitFor(() =>
      expect(Namespaces.updateNamespaceNsfw).toHaveBeenLastCalledWith(
        expect.objectContaining({ body: { threshold: 0.9 } }),
      ),
    );

    fireEvent.click(await screen.findByRole("button", { name: "Classify existing (4)" }));
    await waitFor(() => expect(Namespaces.classifyNamespaceNsfw).toHaveBeenCalled());
  });

  it("is read-only for people who don't own the namespace", async () => {
    editor = false;
    m(Namespaces.getNamespaceNsfw).mockReturnValue(ok({ ...STATUS, enabled: true }));
    wrap(<NsfwSection ns="pods" isOwner={false} />);
    expect(await screen.findByLabelText("Flag at score")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Classify existing (4)" })).toHaveAttribute("aria-disabled", "true");
  });
});
