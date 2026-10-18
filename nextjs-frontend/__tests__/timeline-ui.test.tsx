import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom";
import type { ReactNode } from "react";

import { Activities } from "@/app/openapi-client";
import type { ActivityPage, StreamActivity } from "@/app/openapi-client/types.gen";
import { ASK_ABOUT_EVENT } from "@/components/page-chat/page-chat-model";
import { LatestCard } from "@/components/timeline/latest-card";
import { TimelinePage } from "@/components/timeline/timeline-page";
import { ToastProvider } from "@/components/ui/toast";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Activities: {
    listActivities: jest.fn(),
    listActivityKinds: jest.fn(),
    markActivities: jest.fn(),
    reactToActivity: jest.fn(),
    listActivityComments: jest.fn(),
    commentOnActivity: jest.fn(),
    deleteActivityComment: jest.fn(),
  },
}));
const replace = jest.fn();
let search = "";
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn(), replace }),
  usePathname: () => "/timeline",
  useSearchParams: () => new URLSearchParams(search),
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("@/lib/hooks/session", () => ({
  useArchive: () => ({
    admin: false,
    namespaces: [
      { id: 3, name: "family" },
      { id: 4, name: "work" },
    ],
  }),
}));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const m = (f: unknown) => f as jest.Mock;
const NOW = Date.now();

function act_(
  id: string,
  minutesAgo: number,
  kind = "email.received",
  actor = ["lens:source/1", "Work Gmail"],
): StreamActivity {
  return {
    id,
    type: "Create",
    kind,
    published: new Date(NOW - minutesAgo * 60_000).toISOString(),
    actor: { type: "Service", id: actor[0], name: actor[1] },
    object: {
      type: "Note",
      id: `lens:recording/${id}`,
      name: `Mail ${id}`,
      url: `/resources/${id}`,
      subject: `Mail ${id}`,
    },
    summary: `${actor[1]} received “Mail ${id}”`,
    space: 3,
    tag: [],
    lens: { source: "recordings", pos: [] },
  };
}

// three emails from one mailbox within minutes (one line), then an upload by a person an hour earlier
const ITEMS = [act_("e3", 1), act_("e2", 2), act_("e1", 3), act_("u1", 90, "asset.added", ["lens:account/1", "Adeel"])];
const PAGE: ActivityPage = {
  items: ITEMS,
  next: null,
  namespaces: { "3": "family" },
  marks: { u1: { reviewed: true } },
  reactions: { e2: [{ emoji: "👍", count: 2, mine: false }], e1: [{ emoji: "👍", count: 1, mine: true }] },
  comments: { u1: 1 },
};
const COMMENT = {
  id: 7,
  recording: 1,
  activity: "u1",
  parent: null,
  text: "Nice one",
  resolved: false,
  created_by: "sam@x.io",
  created_by_name: "Sam",
  created_at: new Date(NOW - 60_000).toISOString(),
  mine: false,
  can_resolve: false,
  can_delete: false,
};

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
  search = "";
  localStorage.clear();
  m(Activities.listActivityKinds).mockReturnValue(
    ok([
      { name: "email.received", label: "Email received", source: "x", type: "Create", object_type: "Note", schema: {} },
      {
        name: "asset.added",
        label: "Asset added",
        source: "recordings",
        type: "Create",
        object_type: "Note",
        schema: {},
      },
    ]),
  );
  m(Activities.markActivities).mockImplementation(({ body }) =>
    ok({ marks: Object.fromEntries(body.ids.map((id: string) => [id, { reviewed: !!body.reviewed }])), unknown: [] }),
  );
  // the first page, then nothing new when it looks again
  m(Activities.listActivities).mockImplementation(({ query }) =>
    ok(query.since ? { items: [], next: null, namespaces: {}, marks: {} } : PAGE),
  );
  m(Activities.reactToActivity).mockImplementation(({ body }) =>
    ok({ reactions: body.on ? [{ emoji: body.emoji, count: 1, mine: true }] : [] }),
  );
  m(Activities.listActivityComments).mockReturnValue(ok([COMMENT]));
  m(Activities.commentOnActivity).mockImplementation(({ body }) =>
    ok({ ...COMMENT, id: 8, text: body.text, parent: body.parent ?? null, mine: true, can_delete: true }),
  );
});

test("reactions and comments: a line sums its items', a 👍 goes to the server, and Enter sends a comment", async () => {
  wrap(<TimelinePage />);
  const rows = within(await screen.findByRole("list", { name: "Timeline" })).getAllByTestId("timeline-row");
  // closed: the run of three emails shows 👍 3 (two on e2, yours on e1); the upload shows its one comment
  expect(rows[0]).toHaveTextContent("👍3");
  expect(rows[1]).toHaveTextContent("1 comment");
  // opened, each item has its own bar: react on e3 from the picker
  fireEvent.click(within(rows[0]).getByRole("button", { name: "Show the 3" }));
  const bars = within(rows[0]).getAllByTestId("reactions");
  expect(bars).toHaveLength(3);
  expect(within(bars[2]).getByRole("button", { name: "thumbs up 1, yours" })).toHaveAttribute("aria-pressed", "true");
  fireEvent.click(within(bars[0]).getByRole("button", { name: "React" }));
  fireEvent.click(await screen.findByRole("button", { name: "party" }));
  await waitFor(() =>
    expect(m(Activities.reactToActivity)).toHaveBeenCalledWith(
      expect.objectContaining({ path: { activity_id: "e3" }, body: { emoji: "🎉", on: true } }),
    ),
  );
  expect(within(bars[0]).getByRole("button", { name: "party 1, yours" })).toHaveAttribute("aria-pressed", "true");
  // a chip of yours takes it back
  fireEvent.click(within(bars[2]).getByRole("button", { name: "thumbs up 1, yours" }));
  await waitFor(() =>
    expect(m(Activities.reactToActivity)).toHaveBeenLastCalledWith(
      expect.objectContaining({ path: { activity_id: "e1" }, body: { emoji: "👍", on: false } }),
    ),
  );
  expect(within(bars[2]).queryByRole("button", { name: /thumbs up/ })).not.toBeInTheDocument();
  // the upload's thread: its comment, then yours sent with Enter
  fireEvent.click(within(rows[1]).getByRole("button", { name: "Show details" }));
  fireEvent.click(within(rows[1]).getByRole("button", { name: "1 comment" }));
  expect(await within(rows[1]).findByText("Nice one")).toBeInTheDocument();
  expect(within(rows[1]).getByText("Sam")).toBeInTheDocument();
  const box = within(rows[1]).getByRole("textbox", { name: "Comment" });
  fireEvent.change(box, { target: { value: "Thanks!" } });
  fireEvent.keyDown(box, { key: "Enter" });
  await waitFor(() =>
    expect(m(Activities.commentOnActivity)).toHaveBeenCalledWith(
      expect.objectContaining({ path: { activity_id: "u1" }, body: { text: "Thanks!", parent: null } }),
    ),
  );
  await waitFor(() => expect(box).toHaveValue(""));
  expect(within(rows[1]).getByRole("button", { name: "2 comments" })).toBeInTheDocument();
  // a reply goes on the thread
  fireEvent.click(within(rows[1]).getByRole("button", { name: "Reply" }));
  const reply = within(rows[1]).getByRole("textbox", { name: "Reply" });
  fireEvent.change(reply, { target: { value: "Agreed" } });
  fireEvent.keyDown(reply, { key: "Enter" });
  await waitFor(() =>
    expect(m(Activities.commentOnActivity)).toHaveBeenLastCalledWith(
      expect.objectContaining({ body: { text: "Agreed", parent: 7 } }),
    ),
  );
});

test("the list groups a run of emails into one line that opens to its items, and shows what you reviewed", async () => {
  wrap(<TimelinePage />);
  const list = await screen.findByRole("list", { name: "Timeline" });
  const rows = within(list).getAllByTestId("timeline-row");
  expect(rows).toHaveLength(2);
  expect(rows[0]).toHaveTextContent("Work Gmail received 3 emails");
  expect(rows[0]).toHaveTextContent("family");
  expect(rows[1]).toHaveTextContent("Adeel received “Mail u1”");
  expect(within(rows[1]).getByRole("button", { name: "Reviewed (r)" })).toHaveAttribute("aria-pressed", "true");
  // the page asks for what isn't hidden
  expect(m(Activities.listActivities).mock.calls[0][0].query).toMatchObject({ hidden: false, limit: 50 });
  fireEvent.click(within(rows[0]).getByRole("button", { name: "Show the 3" }));
  expect(within(rows[0]).getByRole("link", { name: "Work Gmail received “Mail e1”" })).toHaveAttribute(
    "href",
    "/resources/e1",
  );
  // one item opens to its details
  fireEvent.click(within(rows[1]).getByRole("button", { name: "Show details" }));
  expect(within(rows[1]).getByText("Subject")).toBeInTheDocument();
  expect(within(rows[1]).getByRole("link", { name: "Open" })).toHaveAttribute("href", "/resources/u1");
});

test("marks: a toggle marks every item of a line, and Mark all shown as reviewed can be undone", async () => {
  wrap(<TimelinePage />);
  const rows = within(await screen.findByRole("list", { name: "Timeline" })).getAllByTestId("timeline-row");
  fireEvent.click(within(rows[0]).getByRole("button", { name: "Mark important (i)" }));
  await waitFor(() =>
    expect(m(Activities.markActivities)).toHaveBeenCalledWith(
      expect.objectContaining({ body: { ids: ["e3", "e2", "e1"], important: true } }),
    ),
  );
  expect(within(rows[0]).getByRole("button", { name: "Important (i)" })).toHaveAttribute("aria-pressed", "true");
  m(Activities.markActivities).mockClear();
  fireEvent.click(screen.getByRole("button", { name: "Mark all shown as reviewed" }));
  await waitFor(() =>
    expect(m(Activities.markActivities)).toHaveBeenCalledWith(
      expect.objectContaining({ body: { ids: ["e3", "e2", "e1"], reviewed: true } }),
    ),
  );
  expect(await screen.findByText("Marked 3 reviewed")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Mark all shown as reviewed" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Undo" }));
  await waitFor(() =>
    expect(m(Activities.markActivities)).toHaveBeenLastCalledWith(
      expect.objectContaining({ body: { ids: ["e3", "e2", "e1"], reviewed: false } }),
    ),
  );
  expect(within(rows[0]).getByRole("button", { name: "Mark reviewed (r)" })).toHaveAttribute("aria-pressed", "false");
});

test("keys: j and k move, r marks reviewed, i marks important", async () => {
  wrap(<TimelinePage />);
  const rows = within(await screen.findByRole("list", { name: "Timeline" })).getAllByTestId("timeline-row");
  fireEvent.keyDown(window, { key: "j" });
  expect(rows[0]).toHaveFocus();
  fireEvent.keyDown(window, { key: "j" });
  expect(rows[1]).toHaveFocus();
  fireEvent.keyDown(window, { key: "r" }); // u1 was reviewed: r clears it
  await waitFor(() =>
    expect(m(Activities.markActivities)).toHaveBeenCalledWith(
      expect.objectContaining({ body: { ids: ["u1"], reviewed: false } }),
    ),
  );
  fireEvent.keyDown(window, { key: "k" });
  expect(rows[0]).toHaveFocus();
  fireEvent.keyDown(window, { key: "i" });
  await waitFor(() =>
    expect(m(Activities.markActivities)).toHaveBeenCalledWith(
      expect.objectContaining({ body: { ids: ["e3", "e2", "e1"], important: true } }),
    ),
  );
  // typing in a field isn't a key for the page
  m(Activities.markActivities).mockClear();
  const box = screen.getByRole("searchbox", { name: "Words in the summary" });
  fireEvent.keyDown(box, { key: "r" });
  expect(m(Activities.markActivities)).not.toHaveBeenCalled();
});

test("asking about an item hands the page chat its id, line and fields, by button or the a key", async () => {
  const asked: string[] = [];
  const listen = (e: Event) => asked.push((e as CustomEvent<string>).detail);
  window.addEventListener(ASK_ABOUT_EVENT, listen);
  try {
    wrap(<TimelinePage />);
    const rows = within(await screen.findByRole("list", { name: "Timeline" })).getAllByTestId("timeline-row");
    fireEvent.click(within(rows[1]).getByRole("button", { name: "Show details" }));
    fireEvent.click(within(rows[1]).getByRole("button", { name: "Ask about this" }));
    expect(asked).toHaveLength(1);
    expect(asked[0]).toContain("Timeline item u1 (asset.added, ");
    expect(asked[0]).toContain("Adeel: Adeel received “Mail u1”");
    expect(asked[0]).toContain("Opens at /resources/u1");
    expect(asked[0]).toContain("Subject: Mail u1");
    // a on a line of several asks about all of them
    fireEvent.keyDown(window, { key: "j" });
    fireEvent.keyDown(window, { key: "a" });
    expect(asked).toHaveLength(2);
    expect(asked[1].match(/^Timeline item /gm)).toHaveLength(3);
    expect(asked[1]).toContain("Timeline item e1 ");
    // typing a in a field isn't a key for the page
    fireEvent.keyDown(screen.getByRole("searchbox", { name: "Words in the summary" }), { key: "a" });
    expect(asked).toHaveLength(2);
  } finally {
    window.removeEventListener(ASK_ABOUT_EVENT, listen);
  }
});

test("hiding takes an item out of sight, with an undo", async () => {
  wrap(<TimelinePage />);
  const rows = within(await screen.findByRole("list", { name: "Timeline" })).getAllByTestId("timeline-row");
  fireEvent.click(within(rows[1]).getByRole("button", { name: "Show details" }));
  fireEvent.click(within(rows[1]).getByRole("button", { name: "Hide" }));
  await waitFor(() => expect(screen.getAllByTestId("timeline-row")).toHaveLength(1));
  expect(m(Activities.markActivities)).toHaveBeenCalledWith(
    expect.objectContaining({ body: { ids: ["u1"], hidden: true } }),
  );
  fireEvent.click(await screen.findByRole("button", { name: "Undo" }));
  await waitFor(() => expect(screen.getAllByTestId("timeline-row")).toHaveLength(2));
});

test("filters go in the address, and new items wait behind a pill", async () => {
  m(Activities.listActivities).mockImplementation(({ query }) =>
    ok(
      query.since
        ? { items: [act_("e4", 0)], next: null, namespaces: { "3": "family" }, marks: {} }
        : { ...PAGE, items: ITEMS.slice(3) },
    ),
  );
  wrap(<TimelinePage />);
  await screen.findByRole("list", { name: "Timeline" });
  expect(screen.getAllByTestId("timeline-row")).toHaveLength(1);
  const pill = await screen.findByRole("button", { name: "1 new" });
  await act(async () => fireEvent.click(pill));
  const rows = screen.getAllByTestId("timeline-row");
  expect(rows).toHaveLength(2);
  expect(rows[0]).toHaveTextContent("Work Gmail received “Mail e4”");
  // a filter changes the address, and is remembered for next time
  fireEvent.click(screen.getByRole("checkbox", { name: "Unreviewed only" }));
  expect(replace).toHaveBeenLastCalledWith("/timeline?unreviewed=1");
  expect(localStorage.getItem("lens.timeline.filters")).toBe("unreviewed=1");
  const words = screen.getByRole("searchbox", { name: "Words in the summary" });
  fireEvent.change(words, { target: { value: "trip" } });
  fireEvent.keyDown(words, { key: "Enter" });
  expect(replace).toHaveBeenLastCalledWith("/timeline?q=trip");
});

test("filters in the address make the query, and an empty address brings back the last ones", async () => {
  localStorage.setItem("lens.timeline.filters", "important=1");
  wrap(<TimelinePage />);
  await waitFor(() => expect(replace).toHaveBeenCalledWith("/timeline?important=1"));
  replace.mockClear();
  search = "unreviewed=1&ns=family";
  m(Activities.listActivities).mockClear();
  wrap(<TimelinePage />);
  await waitFor(() =>
    expect(m(Activities.listActivities).mock.calls[0][0].query).toMatchObject({
      reviewed: false,
      namespace: ["family"],
      hidden: false,
    }),
  );
  expect(replace).not.toHaveBeenCalled();
});

test("For you: the toggle goes in the address, each placed line says why, and Latest stays the default", async () => {
  const first = wrap(<TimelinePage />);
  await screen.findByRole("list", { name: "Timeline" });
  expect(m(Activities.listActivities).mock.calls[0][0].query.order).toBeUndefined();
  expect(screen.queryAllByTestId("why")).toHaveLength(0);
  await act(async () => fireEvent.click(screen.getByRole("radio", { name: "For you" })));
  expect(replace).toHaveBeenLastCalledWith("/timeline?order=for_you");
  first.unmount();
  // in that order the page comes ranked, with why on what the person's signals placed
  search = "order=for_you";
  m(Activities.listActivities).mockImplementation(({ query }) =>
    ok(
      query.since
        ? { items: [], next: null, namespaces: {}, marks: {} }
        : { ...PAGE, items: [ITEMS[3], ...ITEMS.slice(0, 3)], why: { u1: "You often react to Adeel" } },
    ),
  );
  wrap(<TimelinePage />);
  const list = await screen.findByRole("list", { name: "Timeline" });
  expect(m(Activities.listActivities).mock.calls.at(-1)?.[0].query.order).toBe("for_you");
  const rows = within(list).getAllByTestId("timeline-row");
  expect(rows[0]).toHaveTextContent("You often react to Adeel");
  expect(within(rows[1]).queryByTestId("why")).not.toBeInTheDocument();
  expect(screen.getByRole("radio", { name: "For you" })).toHaveAttribute("aria-checked", "true");
});

test("Home's Latest card lists what you haven't reviewed", async () => {
  m(Activities.listActivities).mockReturnValue(
    ok({ items: ITEMS.slice(0, 2), next: null, namespaces: { "3": "family" } }),
  );
  wrap(<LatestCard />);
  expect(await screen.findByRole("link", { name: "Work Gmail received “Mail e3”" })).toHaveAttribute(
    "href",
    "/resources/e3",
  );
  expect(m(Activities.listActivities).mock.calls[0][0].query).toEqual({ reviewed: false, hidden: false, limit: 5 });
  expect(screen.getByRole("link", { name: "Timeline" })).toHaveAttribute("href", "/timeline?unreviewed=1");
  expect(screen.queryByRole("button", { name: /Mark/ })).not.toBeInTheDocument();
});
