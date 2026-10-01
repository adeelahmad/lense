import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom";
import { useState } from "react";

import type { Comment, Highlight } from "@/app/openapi-client/types.gen";
import { CommentsTab } from "@/components/recording/comments-tab";
import { RecordingProvider, type PanelTab, type RecordingCtx } from "@/components/recording/context";
import { HighlightsTab } from "@/components/recording/highlights-tab";
import { PlayerProvider } from "@/components/player/media";
import { TooltipProvider } from "@/components/ui/tooltip";

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const api = {
  listComments: jest.fn<Promise<unknown>, [unknown]>(),
  createComment: jest.fn<Promise<unknown>, [unknown]>(),
  updateComment: jest.fn<Promise<unknown>, [unknown]>(),
  deleteComment: jest.fn<Promise<unknown>, [unknown]>(),
  listHighlights: jest.fn<Promise<unknown>, [unknown]>(),
  createHighlight: jest.fn<Promise<unknown>, [unknown]>(),
  updateHighlight: jest.fn<Promise<unknown>, [unknown]>(),
  deleteHighlight: jest.fn<Promise<unknown>, [unknown]>(),
};
jest.mock("@/app/openapi-client", () => ({
  Comments: {
    listComments: (a: unknown) => api.listComments(a),
    createComment: (a: unknown) => api.createComment(a),
    updateComment: (a: unknown) => api.updateComment(a),
    deleteComment: (a: unknown) => api.deleteComment(a),
    listHighlights: (a: unknown) => api.listHighlights(a),
    createHighlight: (a: unknown) => api.createHighlight(a),
    updateHighlight: (a: unknown) => api.updateHighlight(a),
    deleteHighlight: (a: unknown) => api.deleteHighlight(a),
  },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
const toast = jest.fn();
jest.mock("@/components/ui/toast", () => ({ useToast: () => toast }));

const comment = (over: Partial<Comment>): Comment => ({
  id: 1,
  recording: 9,
  parent: null,
  text: "Is this claim sourced?",
  t0: 4000,
  t1: 9000,
  quote: "the capsid model beat",
  resolved: false,
  created_by: "vi@x.io",
  created_by_name: null,
  created_at: "2026-09-30T10:00:00Z",
  mine: true,
  can_resolve: true,
  can_delete: true,
  ...over,
});
const highlight = (over: Partial<Highlight>): Highlight => ({
  id: 1,
  recording: 9,
  t0: 4000,
  t1: 9000,
  quote: "the capsid model beat",
  colour: "yellow",
  label: "Key claim",
  created_by: "ed@x.io",
  created_by_name: "Ana",
  created_at: "2026-09-30T10:00:00Z",
  mine: false,
  can_edit: true,
  ...over,
});

function Harness({ tab, canEdit, draft }: { tab: PanelTab; canEdit: boolean; draft: boolean }) {
  const [commentDraft, setDraft] = useState(draft ? { t0: 4000, t1: 9000, quote: "the capsid" } : null);
  const ctx = {
    id: 9,
    ns: "pods",
    canEdit,
    paged: false,
    where: (ms: number) => `${ms}`,
    commentDraft,
    clearCommentDraft: () => setDraft(null),
    highlightFocus: 2,
    focusHighlight: () => {},
  } as unknown as RecordingCtx;
  return <RecordingProvider value={ctx}>{tab === "comments" ? <CommentsTab /> : <HighlightsTab />}</RecordingProvider>;
}

function show(tab: PanelTab, { canEdit = true, draft = false } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <PlayerProvider hasMedia durationMs={60000}>
          <Harness tab={tab} canEdit={canEdit} draft={draft} />
        </PlayerProvider>
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

beforeAll(() => {
  Element.prototype.scrollIntoView = jest.fn();
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});
beforeEach(() => {
  jest.clearAllMocks();
  api.listComments.mockImplementation(() =>
    ok([
      comment({}),
      comment({
        id: 2,
        parent: 1,
        text: "Yes, the paper",
        t0: null,
        t1: null,
        quote: null,
        mine: false,
        created_by_name: "Ana",
        can_resolve: false,
        can_delete: false,
      }),
      comment({
        id: 3,
        text: "Old thread",
        t0: null,
        t1: null,
        quote: null,
        resolved: true,
        resolved_by: "ed@x.io",
        resolved_by_name: "Ana",
        resolved_at: "2026-09-30T11:00:00Z",
      }),
    ]),
  );
  api.listHighlights.mockImplementation(() =>
    ok([highlight({}), highlight({ id: 2, t0: 12000, t1: 12000, colour: "green", label: null, quote: null })]),
  );
  api.createComment.mockImplementation((a) => ok(comment({ id: 4, ...((a as { body: object }).body as object) })));
  api.updateComment.mockImplementation((a) => ok(comment({ ...((a as { body: object }).body as object) })));
  api.deleteComment.mockImplementation(() => ok({ ok: true }));
  api.updateHighlight.mockImplementation((a) => ok(highlight({ ...((a as { body: object }).body as object) })));
  api.deleteHighlight.mockImplementation(() => ok({ ok: true }));
});

describe("the Comments tab", () => {
  it("shows the open threads with their replies, and the resolved ones on request", async () => {
    show("comments");
    const thread = await screen.findByRole("listitem", { name: "Thread" });
    expect(within(thread).getByText("Is this claim sourced?")).toBeInTheDocument();
    expect(within(thread).getByText("“the capsid model beat”")).toBeInTheDocument();
    expect(within(thread).getByRole("button", { name: "Play from 0:04–0:09" })).toBeInTheDocument();
    expect(within(thread).getByRole("list", { name: "Replies" })).toHaveTextContent("Ana");
    expect(within(thread).getByRole("list", { name: "Replies" })).toHaveTextContent("Yes, the paper");
    expect(screen.queryByText("Old thread")).not.toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Show 1 resolved thread"));
    const old = screen.getByRole("listitem", { name: "Resolved thread" });
    expect(within(old).getByText("Old thread")).toBeInTheDocument();
    expect(within(old).getByText(/Resolved by Ana/)).toBeInTheDocument();
  });

  it("comments on the words picked in the text, and replies on a thread", async () => {
    show("comments", { draft: true });
    await screen.findByRole("listitem", { name: "Thread" });
    const form = screen.getByRole("form", { name: "New comment" });
    expect(within(form).getByText("“the capsid”")).toBeInTheDocument();
    fireEvent.change(within(form).getByRole("textbox", { name: "Comment" }), { target: { value: " Sourced? " } });
    fireEvent.click(within(form).getByRole("button", { name: "Comment" }));
    await waitFor(() => expect(api.createComment).toHaveBeenCalledTimes(1));
    expect(api.createComment.mock.calls[0][0]).toMatchObject({
      path: { rid: 9 },
      body: { text: "Sourced?", t0: 4000, t1: 9000, quote: "the capsid" },
    });
    await waitFor(() => expect(toast).toHaveBeenCalledWith({ title: "Comment added", tone: "green" }));

    const thread = screen.getByRole("listitem", { name: "Thread" });
    fireEvent.click(within(thread).getByRole("button", { name: "Reply" }));
    const reply = within(thread).getByRole("form", { name: "Reply" });
    fireEvent.change(within(reply).getByRole("textbox", { name: "Reply" }), { target: { value: "Thanks" } });
    fireEvent.keyDown(within(reply).getByRole("textbox", { name: "Reply" }), { key: "Enter", metaKey: true });
    await waitFor(() => expect(api.createComment).toHaveBeenCalledTimes(2));
    expect(api.createComment.mock.calls[1][0]).toMatchObject({ body: { text: "Thanks", parent: 1 } });
  });

  it("resolves a thread, edits and deletes a comment, and says who may do what", async () => {
    show("comments");
    const thread = await screen.findByRole("listitem", { name: "Thread" });
    const open = (name: string) =>
      fireEvent.keyDown(within(thread).getAllByRole("button", { name })[0], { key: "Enter" });
    open("Comment actions");
    fireEvent.click(await screen.findByRole("menuitem", { name: "Resolve" }));
    await waitFor(() => expect(api.updateComment).toHaveBeenCalledTimes(1));
    expect(api.updateComment.mock.calls[0][0]).toMatchObject({ path: { rid: 9, cid: 1 }, body: { resolved: true } });

    open("Comment actions");
    fireEvent.click(await screen.findByRole("menuitem", { name: "Edit" }));
    const edit = await within(thread).findByRole("form", { name: "Edit comment" });
    fireEvent.change(within(edit).getByRole("textbox", { name: "Comment" }), {
      target: { value: "Sourced anywhere?" },
    });
    fireEvent.click(within(edit).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.updateComment).toHaveBeenCalledTimes(2));
    expect(api.updateComment.mock.calls[1][0]).toMatchObject({ body: { text: "Sourced anywhere?" } });

    // the reply is Ana's: only she edits it, only she or an owner deletes it, and a reply isn't resolved
    open("Reply actions");
    const menu = await screen.findByRole("menu");
    expect(within(menu).getByRole("menuitem", { name: "Only its writer can edit it" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    expect(within(menu).getByRole("menuitem", { name: "Only Ana or an owner of pods can delete it" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    expect(within(menu).queryByRole("menuitem", { name: /Resolve/ })).not.toBeInTheDocument();
    fireEvent.keyDown(menu, { key: "Escape" });

    open("Comment actions");
    fireEvent.click(await screen.findByRole("menuitem", { name: "Delete…" }));
    expect(await within(thread).findByRole("alert")).toHaveTextContent(
      "Delete your comment and its reply for everyone?",
    );
    fireEvent.click(within(thread).getByRole("button", { name: "Delete" }));
    await waitFor(() =>
      expect(api.deleteComment).toHaveBeenCalledWith(expect.objectContaining({ path: { rid: 9, cid: 1 } })),
    );
  });
});

describe("the Highlights tab", () => {
  it("lists them with their colour and label; editors recolour, relabel and remove them", async () => {
    show("highlights");
    const list = await screen.findByRole("list", { name: "Highlights" });
    const [first, second] = within(list).getAllByRole("listitem");
    expect(first).toHaveAccessibleName("Key claim");
    expect(within(first).getByText("the capsid model beat")).toBeInTheDocument();
    expect(within(first).getByRole("button", { name: "Yellow" })).toHaveAttribute("aria-pressed", "true");
    expect(within(first).getByRole("button", { name: "Play from 0:04–0:09" })).toBeInTheDocument();
    expect(second).toHaveAccessibleName("Green highlight");
    expect(second).toHaveClass("bg-hl"); // the one chosen on the text

    fireEvent.click(within(first).getByRole("button", { name: "Blue" }));
    await waitFor(() => expect(api.updateHighlight).toHaveBeenCalledTimes(1));
    expect(api.updateHighlight.mock.calls[0][0]).toMatchObject({ path: { rid: 9, hid: 1 }, body: { colour: "blue" } });

    const label = within(first).getByRole("textbox", { name: "Label" });
    fireEvent.change(label, { target: { value: "Disputed claim" } });
    fireEvent.keyDown(label, { key: "Enter" });
    await waitFor(() => expect(api.updateHighlight).toHaveBeenCalledTimes(2));
    expect(api.updateHighlight.mock.calls[1][0]).toMatchObject({ body: { label: "Disputed claim" } });

    fireEvent.click(within(second).getByRole("button", { name: "Remove highlight" }));
    await waitFor(() =>
      expect(api.deleteHighlight).toHaveBeenCalledWith(expect.objectContaining({ path: { rid: 9, hid: 2 } })),
    );
  });

  it("shows readers the highlights and why they can't change them", async () => {
    api.listHighlights.mockImplementation(() => ok([highlight({ can_edit: false })]));
    show("highlights", { canEdit: false });
    const list = await screen.findByRole("list", { name: "Highlights" });
    const [first] = within(list).getAllByRole("listitem");
    expect(within(first).queryByRole("group", { name: "Colour" })).not.toBeInTheDocument();
    expect(within(first).getByText("Key claim")).toBeInTheDocument();
    expect(within(first).getByRole("button", { name: "Remove highlight" })).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByText(/Editors of pods make them/)).toBeInTheDocument();
  });
});
