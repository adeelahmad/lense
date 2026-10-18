import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom";
import type { ReactNode } from "react";

import { Feeds } from "@/app/openapi-client";
import type { Feed, FeedItem } from "@/app/openapi-client/types.gen";
import { FeedPage } from "@/components/feeds/feed-page";
import { FeedsScreen } from "@/components/feeds/feeds-screen";
import { ToastProvider } from "@/components/ui/toast";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Feeds: {
    listFeeds: jest.fn(),
    createFeed: jest.fn(),
    getFeed: jest.fn(),
    updateFeed: jest.fn(),
    deleteFeed: jest.fn(),
    rotateFeedToken: jest.fn(),
    listFeedItems: jest.fn(),
    publishToFeed: jest.fn(),
    removeFeedItem: jest.fn(),
  },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
const push = jest.fn();
const replace = jest.fn();
jest.mock("next/navigation", () => ({ useRouter: () => ({ push, replace }) }));
jest.mock("@/lib/hooks/session", () => ({
  useArchive: () => ({
    admin: true,
    namespace: "home",
    namespaces: [
      { id: 3, name: "home" },
      { id: 4, name: "work" },
    ],
    can: () => true,
  }),
}));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const m = (f: unknown) => f as jest.Mock;
let writeText: jest.Mock;
beforeAll(() => {
  // Radix's checkbox measures itself; jsdom has no ResizeObserver
  global.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});
beforeEach(() => {
  jest.clearAllMocks();
  writeText = jest.fn(() => Promise.resolve());
  Object.assign(navigator, { clipboard: { writeText } });
});

const WEEKLY: Feed = {
  id: 7,
  space: 3,
  namespace: "home",
  kind: "podcast",
  title: "Weekly",
  author: "Lens",
  explicit: false,
  max_items: 50,
  url: "https://lens.local/api/v1/feeds/public/lfd_secret1",
  path: "/api/v1/feeds/public/lfd_secret1",
  page: "/feeds/7",
  items: 2,
  last_item_at: "2026-10-17T10:00:00+00:00",
  fetched_at: null,
  created_at: "2026-10-10T10:00:00+00:00",
};
const NEWS: Feed = {
  ...WEEKLY,
  id: 8,
  kind: "rss",
  title: "What’s new",
  items: 0,
  fetched_at: "2026-10-17T12:00:00+00:00",
};
const ITEMS: FeedItem[] = [
  {
    id: 1,
    feed: 7,
    kind: "episode",
    guid: "recording/31",
    title: "Capsids and shipments",
    summary: "Two hosts on the week’s papers.",
    published_at: "2026-10-17T10:00:00+00:00",
    recording: 31,
    by: "routine:2",
  },
  {
    id: 2,
    feed: 7,
    kind: "text",
    guid: "text/digest-41",
    title: "Weekly digest",
    summary: "2 new this week",
    link: "https://example.org/digest/41",
    published_at: "2026-10-16T10:00:00+00:00",
  },
];

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

test("the Feeds page lists each feed with its kind, namespace, items and whether an app reads it", async () => {
  m(Feeds.listFeeds).mockReturnValue(ok([WEEKLY, NEWS]));
  wrap(<FeedsScreen />);
  const weekly = await screen.findByRole("link", { name: /Weekly/ });
  expect(weekly).toHaveAttribute("href", "/feeds/7");
  expect(within(weekly).getByText("Podcast")).toBeInTheDocument();
  expect(within(weekly).getByText("home")).toBeInTheDocument();
  expect(within(weekly).getByText("2 items")).toBeInTheDocument();
  expect(within(weekly).getByText("not read by an app yet")).toBeInTheDocument();
  const news = screen.getByRole("link", { name: /What’s new/ });
  expect(within(news).getByText("RSS")).toBeInTheDocument();
  expect(within(news).getByText("Nothing in it yet")).toBeInTheDocument();
  expect(within(news).getByText(/^read/)).toBeInTheDocument();
  // the top bar's namespace narrows the list
  expect(m(Feeds.listFeeds).mock.calls[0][0].query).toEqual({ namespace: "home" });
});

test("New feed opens on the title, defaults to the current namespace, and Enter makes it and opens its page", async () => {
  m(Feeds.listFeeds).mockReturnValue(ok([]));
  m(Feeds.createFeed).mockReturnValue(ok({ ...NEWS, id: 9, title: "Inbox" }));
  wrap(<FeedsScreen />);
  expect(await screen.findByText("No feeds yet")).toBeInTheDocument();
  fireEvent.click(screen.getAllByRole("button", { name: "New feed" })[0]);
  const title = await screen.findByLabelText("Title");
  await waitFor(() => expect(title).toHaveFocus());
  expect(screen.getByLabelText("Namespace")).toHaveValue("home");
  expect(screen.getByLabelText("Kind")).toHaveValue("rss");
  fireEvent.change(title, { target: { value: "Inbox" } });
  fireEvent.keyDown(title, { key: "Enter" });
  await waitFor(() => expect(Feeds.createFeed).toHaveBeenCalledTimes(1));
  expect(m(Feeds.createFeed).mock.calls[0][0].body).toEqual({ title: "Inbox", kind: "rss", namespace: "home" });
  await waitFor(() => expect(push).toHaveBeenCalledWith("/feeds/9"));
  expect(await screen.findByText("Feed made")).toBeInTheDocument();
});

test("a feed's page copies the address, gets a new one, and takes an item out with Undo", async () => {
  m(Feeds.getFeed).mockReturnValue(ok(WEEKLY));
  m(Feeds.listFeedItems).mockReturnValue(ok(ITEMS));
  const rotated = { ...WEEKLY, url: "https://lens.local/api/v1/feeds/public/lfd_secret2" };
  m(Feeds.rotateFeedToken).mockImplementation(() => {
    m(Feeds.getFeed).mockReturnValue(ok(rotated));
    return ok(rotated);
  });
  m(Feeds.removeFeedItem).mockReturnValue(ok({ ok: true }));
  m(Feeds.publishToFeed).mockReturnValue(ok({ added: [1], already: 0, refused: [] }));
  wrap(<FeedPage id={7} />);
  expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent("Weekly");
  expect(screen.getByText("Podcast feed")).toBeInTheDocument();
  expect(screen.getByText(WEEKLY.url)).toBeInTheDocument();
  expect(screen.getByText("No app has read it yet.", { exact: false })).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Copy the address" }));
  await waitFor(() => expect(writeText).toHaveBeenCalledWith(WEEKLY.url));

  fireEvent.click(screen.getByRole("button", { name: "New address" }));
  expect(await screen.findByText("https://lens.local/api/v1/feeds/public/lfd_secret2")).toBeInTheDocument();
  expect(m(Feeds.rotateFeedToken).mock.calls[0][0].path).toEqual({ fid: 7 });
  expect(screen.getByText("New address", { selector: "div" })).toBeInTheDocument();

  // the items: an episode made by a routine (a link into Lens) and a text item (an outside link)
  expect(screen.getByRole("link", { name: "Capsids and shipments" })).toHaveAttribute("href", "/podcasts/31");
  expect(screen.getByText("by a routine")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /Weekly digest/ })).toHaveAttribute("href", "https://example.org/digest/41");

  fireEvent.click(screen.getByRole("button", { name: "Remove Capsids and shipments" }));
  await waitFor(() => expect(Feeds.removeFeedItem).toHaveBeenCalledTimes(1));
  expect(m(Feeds.removeFeedItem).mock.calls[0][0].path).toEqual({ fid: 7, iid: 1 });
  fireEvent.click(await screen.findByRole("button", { name: "Undo" }));
  await waitFor(() => expect(Feeds.publishToFeed).toHaveBeenCalledTimes(1));
  expect(m(Feeds.publishToFeed).mock.calls[0][0]).toMatchObject({ path: { fid: 7 }, body: { recordings: [31] } });
});

test("deleting a feed is undone by making it again with its items, at a new address", async () => {
  m(Feeds.getFeed).mockReturnValue(ok(WEEKLY));
  m(Feeds.listFeedItems).mockReturnValue(ok(ITEMS));
  m(Feeds.deleteFeed).mockReturnValue(ok({ ok: true }));
  m(Feeds.createFeed).mockReturnValue(ok({ ...WEEKLY, id: 12, items: 0 }));
  m(Feeds.publishToFeed).mockReturnValue(ok({ added: [5, 6], already: 0, refused: [] }));
  wrap(<FeedPage id={7} />);
  await screen.findByRole("link", { name: "Capsids and shipments" });
  fireEvent.click(screen.getByRole("button", { name: "Delete" }));
  await waitFor(() => expect(Feeds.deleteFeed).toHaveBeenCalledTimes(1));
  expect(await screen.findByText("Deleted “Weekly”")).toBeInTheDocument();
  expect(replace).toHaveBeenCalledWith("/feeds");
  fireEvent.click(screen.getByRole("button", { name: "Undo" }));
  await waitFor(() => expect(Feeds.publishToFeed).toHaveBeenCalledTimes(1));
  expect(m(Feeds.createFeed).mock.calls[0][0].body).toMatchObject({
    namespace: "home",
    kind: "podcast",
    title: "Weekly",
    author: "Lens",
    max_items: 50,
  });
  expect(m(Feeds.publishToFeed).mock.calls[0][0]).toEqual({
    client: expect.anything(),
    path: { fid: 12 },
    body: {
      recordings: [31],
      activities: [],
      texts: [
        {
          title: "Weekly digest",
          summary: "2 new this week",
          link: "https://example.org/digest/41",
          guid: "digest-41",
        },
      ],
    },
  });
  await waitFor(() => expect(replace).toHaveBeenCalledWith("/feeds/12"));
});

test("editors change what readers show, and Enter in a field saves", async () => {
  m(Feeds.getFeed).mockReturnValue(ok(NEWS));
  m(Feeds.listFeedItems).mockReturnValue(ok([]));
  m(Feeds.updateFeed).mockReturnValue(ok({ ...NEWS, author: "Ada" }));
  wrap(<FeedPage id={8} />);
  const author = await screen.findByLabelText(/^Author/);
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  fireEvent.change(author, { target: { value: "Ada" } });
  expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
  fireEvent.submit(author.closest("form")!);
  await waitFor(() => expect(Feeds.updateFeed).toHaveBeenCalledTimes(1));
  expect(m(Feeds.updateFeed).mock.calls[0][0]).toMatchObject({
    path: { fid: 8 },
    body: { author: "Ada", title: "What’s new" },
  });
  expect(await screen.findByText("Saved")).toBeInTheDocument();
  expect(screen.queryByLabelText("Explicit content")).not.toBeInTheDocument();
  expect(screen.getByText("Nothing in it yet", { selector: "h2" })).toBeInTheDocument();
});
