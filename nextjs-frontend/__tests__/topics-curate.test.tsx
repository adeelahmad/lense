import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Topics } from "@/app/openapi-client";
import { findTopics, TopicMergeInto, topicMergeTitle } from "@/components/topics/curate";
import type { TopicItem } from "@/components/topics/model";
import { TopicDrawer } from "@/components/topics/topic-drawer";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Topics: {
    getTopic: jest.fn(),
    listTopics: jest.fn(),
    mergeTopics: jest.fn(),
    deleteTopic: jest.fn(),
    undoTopicChange: jest.fn(),
  },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("@/lib/hooks/session", () => ({ useArchive: () => ({ can: () => true }), needRole: () => "" }));
jest.mock("@/components/similar/similar-list", () => ({ SimilarSection: () => null }));
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

const topic = (id: number, label: string, recordings = 0, alt: string[] = []): TopicItem => ({
  id,
  label,
  alt,
  broader: [],
  related: [],
  recordings,
  narrower: 0,
});
const vocab = [topic(1, "Funding", 5), topic(2, "Grants", 2, ["bursaries"]), topic(3, "Crowdfunding", 1)];

beforeEach(() => jest.clearAllMocks());

describe("finding a topic", () => {
  it("puts labels that start with the text first, then other labels, then any match", () => {
    expect(findTopics(vocab, "fund").map((t) => t.label)).toEqual(["Funding", "Crowdfunding"]);
    expect(findTopics(vocab, "burs").map((t) => t.label)).toEqual(["Grants"]);
    expect(findTopics(vocab, "", [1]).map((t) => t.label)).toEqual(["Grants", "Crowdfunding"]); // the most used first
    expect(findTopics(vocab, "zzz")).toEqual([]);
  });

  it("says what was merged into what", () => {
    expect(topicMergeTitle("Funding", ["Grants"])).toBe("Merged Grants into Funding");
    expect(topicMergeTitle("Funding", ["Grants", "Bursaries"])).toBe("Merged 2 topics into Funding");
  });
});

describe("merging into a topic", () => {
  it("finds one by typing and merges it at once, with Undo on the toast", async () => {
    m(Topics.mergeTopics).mockReturnValue(ok({ ...vocab[0], undo: 12 }));
    m(Topics.undoTopicChange).mockReturnValue(ok({ ok: true }));
    show(<TopicMergeInto keep={{ id: 1, label: "Funding" }} all={vocab} open onOpenChange={() => undefined} />);
    const box = screen.getByRole("combobox", { name: "Topic to merge into Funding" });
    expect(box).toHaveFocus();
    fireEvent.change(box, { target: { value: "gra" } });
    expect(screen.getAllByRole("option")).toHaveLength(1);
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(Topics.mergeTopics).toHaveBeenCalled());
    expect(m(Topics.mergeTopics).mock.calls[0][0].body).toEqual({ keep: 1, others: [2] });
    await waitFor(() => expect(toast).toHaveBeenCalled());
    const t = toast.mock.calls[0][0];
    expect(t.title).toBe("Merged Grants into Funding");
    t.action.onClick();
    await waitFor(() => expect(Topics.undoTopicChange).toHaveBeenCalled());
    expect(m(Topics.undoTopicChange).mock.calls[0][0].path).toEqual({ cid: 12 });
  });
});

describe("the topic drawer", () => {
  const detail = { ...vocab[1], about: [], narrower: [], broader: [], related: [] };

  it("deletes at once, with Undo instead of a confirm", async () => {
    m(Topics.getTopic).mockReturnValue(ok(detail));
    m(Topics.deleteTopic).mockReturnValue(ok({ ok: true, undo: 30 }));
    m(Topics.undoTopicChange).mockReturnValue(ok({ ok: true }));
    const onClose = jest.fn();
    show(<TopicDrawer id={2} ns="pods" all={vocab} onSelect={() => undefined} onClose={onClose} />);
    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
    await waitFor(() => expect(Topics.deleteTopic).toHaveBeenCalled());
    expect(screen.queryByRole("dialog", { name: /Delete/ })).not.toBeInTheDocument();
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    const t = toast.mock.calls[0][0];
    expect(t.title).toBe("Deleted Grants");
    t.action.onClick();
    await waitFor(() => expect(m(Topics.undoTopicChange).mock.calls[0][0].path).toEqual({ cid: 30 }));
  });

  it("opens a find box to merge another topic in", async () => {
    m(Topics.getTopic).mockReturnValue(ok(detail));
    show(<TopicDrawer id={2} ns="pods" all={vocab} onSelect={() => undefined} onClose={() => undefined} />);
    fireEvent.click(await screen.findByRole("button", { name: "Merge" }));
    expect(screen.getByRole("combobox", { name: "Topic to merge into Grants" })).toHaveFocus();
  });
});
