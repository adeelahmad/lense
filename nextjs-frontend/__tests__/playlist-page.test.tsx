import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom";

import type { Clip, PlaylistDetail } from "@/app/openapi-client/types.gen";
import { PlaylistPage } from "@/components/playlists/playlist-page";
import { TooltipProvider } from "@/components/ui/tooltip";

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const api = {
  getPlaylist: jest.fn<Promise<unknown>, [unknown]>(),
  reorderPlaylist: jest.fn<Promise<unknown>, [unknown]>(),
  removeClip: jest.fn<Promise<unknown>, [unknown]>(),
  addClip: jest.fn<Promise<unknown>, [unknown]>(),
  updatePlaylist: jest.fn<Promise<unknown>, [unknown]>(),
  deletePlaylist: jest.fn<Promise<unknown>, [unknown]>(),
  createPlaylist: jest.fn<Promise<unknown>, [unknown]>(),
};
jest.mock("@/app/openapi-client", () => ({
  Playlists: Object.fromEntries(
    [
      "getPlaylist",
      "reorderPlaylist",
      "removeClip",
      "addClip",
      "updatePlaylist",
      "deletePlaylist",
      "createPlaylist",
    ].map((k) => [k, (a: unknown) => api[k as keyof typeof api](a)]),
  ),
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
const push = jest.fn();
jest.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
const toast = jest.fn();
jest.mock("@/components/ui/toast", () => ({ useToast: () => toast }));

const clip = (id: number, over: Partial<Clip> = {}): Clip => ({
  id,
  playlist: 5,
  position: id - 1,
  recording: 10 + id,
  t0: 1000,
  t1: 6000,
  title: `Clip ${id}`,
  recording_title: `Episode ${id}`,
  namespace: "pods",
  media: "audio",
  audio: `/api/v1/recordings/${10 + id}/audio?sig=x`,
  link: `/resources/${10 + id}?t=1`,
  ...over,
});
const playlist = (over: Partial<PlaylistDetail> = {}): PlaylistDetail => ({
  id: 5,
  name: "Talks",
  shared: false,
  mine: true,
  count: 3,
  duration_ms: 15000,
  items: [clip(1), clip(2), clip(3, { audio: null, media: "transcript" })],
  hidden: 0,
  ...over,
});

function show() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <PlaylistPage id={5} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

const titles = () =>
  within(screen.getByRole("list", { name: "Clips" }))
    .getAllByRole("listitem")
    .map((li) => within(li).getByText(/^Clip \d$/).textContent);

beforeAll(() => {
  HTMLMediaElement.prototype.pause = jest.fn();
  HTMLMediaElement.prototype.load = jest.fn();
  HTMLMediaElement.prototype.play = jest.fn(() => Promise.resolve());
});
beforeEach(() => {
  jest.clearAllMocks();
  api.getPlaylist.mockImplementation(() => ok(playlist()));
  api.reorderPlaylist.mockImplementation(() => new Promise(() => undefined));
  api.removeClip.mockImplementation(() => ok({ ok: true }));
  api.addClip.mockImplementation(() => ok(clip(9)));
});

describe("a playlist's page", () => {
  it("lists the clips in order with their resource and times; text clips can't play", async () => {
    show();
    await screen.findByRole("list", { name: "Clips" });
    expect(titles()).toEqual(["Clip 1", "Clip 2", "Clip 3"]);
    expect(screen.getByRole("textbox", { name: "Playlist name" })).toHaveValue("Talks");
    expect(screen.getByRole("button", { name: "Play Clip 1" })).toBeEnabled();
    expect(screen.getByRole("button", { name: /Text only/ })).toBeDisabled();
    expect(screen.getByText("Episode 1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Play all" })).toBeEnabled();
  });

  it("reorders with Up and Down and saves the new order", async () => {
    show();
    await screen.findByRole("list", { name: "Clips" });
    const rows = within(screen.getByRole("list", { name: "Clips" })).getAllByRole("listitem");
    expect(within(rows[0]).getByRole("button", { name: "Move up" })).toBeDisabled();
    fireEvent.click(within(rows[0]).getByRole("button", { name: "Move down" }));
    expect(titles()).toEqual(["Clip 2", "Clip 1", "Clip 3"]);
    await waitFor(() => expect(api.reorderPlaylist).toHaveBeenCalledTimes(1));
    expect(api.reorderPlaylist.mock.calls[0][0]).toMatchObject({ path: { pid: 5 }, body: { items: [2, 1, 3] } });
  });

  it("removes a clip at once, and Undo puts it back in its place", async () => {
    show();
    await screen.findByRole("list", { name: "Clips" });
    const rows = within(screen.getByRole("list", { name: "Clips" })).getAllByRole("listitem");
    api.getPlaylist.mockImplementation(() => ok(playlist({ items: [clip(1), clip(3)] }))); // as the server has it now
    fireEvent.click(within(rows[1]).getByRole("button", { name: "Remove from playlist" }));
    expect(titles()).toEqual(["Clip 1", "Clip 3"]);
    await waitFor(() => expect(toast).toHaveBeenCalledWith(expect.objectContaining({ title: "Removed Clip 2" })));
    expect(api.removeClip.mock.calls[0][0]).toMatchObject({ path: { pid: 5, iid: 2 } });
    toast.mock.calls[0][0].action.onClick();
    await waitFor(() => expect(titles()).toEqual(["Clip 1", "Clip 2", "Clip 3"]));
    await waitFor(() => expect(api.addClip).toHaveBeenCalledTimes(1));
    expect(api.addClip.mock.calls[0][0]).toMatchObject({
      path: { pid: 5 },
      body: { recording: 12, t0: 1000, t1: 6000, title: "Clip 2", position: 1 },
    });
  });

  it("shows someone else's shared playlist without ways to change it", async () => {
    api.getPlaylist.mockImplementation(() => ok(playlist({ mine: false, shared: true, created_by_name: "Ana" })));
    show();
    await screen.findByRole("list", { name: "Clips" });
    expect(screen.queryByRole("textbox", { name: "Playlist name" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Move down" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove from playlist" })).not.toBeInTheDocument();
    expect(screen.getByText(/by Ana/)).toBeInTheDocument();
  });
});
