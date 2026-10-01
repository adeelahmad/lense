import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Files } from "@/app/openapi-client";
import type { ResourceFile, ResourceFiles } from "@/app/openapi-client/types.gen";
import { PlayerProvider } from "@/components/player/media";
import { RecordingProvider, type RecordingCtx } from "@/components/recording/context";
import { FilesTab } from "@/components/recording/files-tab";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Files: {
    listFiles: jest.fn(),
    listFileLines: jest.fn(),
    addFile: jest.fn(),
    updateFile: jest.fn(),
    deleteFile: jest.fn(),
  },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
const toast = jest.fn();
jest.mock("@/components/ui/toast", () => ({ useToast: () => toast }));
const picked: File[] = [];
jest.mock("@/components/import/pending", () => ({ chooseFiles: () => Promise.resolve(picked.splice(0)) }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const m = (f: unknown) => f as jest.Mock;
const file = (over: Partial<ResourceFile>): ResourceFile => ({
  id: 3,
  role: "captions",
  name: "harbour.vtt",
  size: 2048,
  content_type: "text/vtt",
  language: "en",
  label: "English captions",
  description: "From the BBC.",
  lines: 2,
  timed: true,
  public: true,
  created_by: "ed@x.io",
  created_by_name: "Ed Editor",
  created_at: "2026-09-30T10:00:00Z",
  download: "/api/v1/recordings/7/files/3/download?exp=1&sig=x",
  ...over,
});
const LIST: ResourceFiles = {
  primary: {
    name: "interview.mp3",
    kind: "audio",
    size: 3 * 1024 * 1024,
    content_type: "audio/mpeg",
    download: "/api/v1/recordings/7/media?exp=1&sig=y",
  },
  files: [
    file({}),
    file({
      id: 4,
      role: "attachment",
      name: "release.pdf",
      label: null,
      description: null,
      lines: null,
      timed: null,
      public: false,
      content_type: "application/pdf",
      download: "/api/v1/recordings/7/files/4/download?exp=1&sig=z",
    }),
  ],
  max_mb: 50,
  can_change: true,
};
const LINES = {
  total: 2,
  lines: [
    { idx: 0, t0: 1000, t1: 3500, text: "The harbour at dawn.", speaker: null },
    { idx: 1, t0: 4000, t1: 6000, text: "Fishing boats come in.", speaker: "Ann" },
  ],
};

function show({ canEdit = true, fileFocus = null }: { canEdit?: boolean; fileFocus?: RecordingCtx["fileFocus"] } = {}) {
  const onSeek = jest.fn();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const ctx = { id: 7, ns: "pods", canEdit, fileFocus } as unknown as RecordingCtx;
  render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <PlayerProvider hasMedia durationMs={60000} onSeek={onSeek}>
          <RecordingProvider value={ctx}>
            <FilesTab />
          </RecordingProvider>
        </PlayerProvider>
      </TooltipProvider>
    </QueryClientProvider>,
  );
  return { onSeek };
}

/** Open a file's ⋯ menu and choose an item (items run once the menu has closed). */
async function choose(row: string, item: RegExp | string) {
  fireEvent.keyDown(screen.getByRole("button", { name: `Actions for ${row}` }), { key: "Enter" });
  fireEvent.click(await screen.findByRole("menuitem", { name: item }));
}

beforeAll(() => {
  Element.prototype.scrollIntoView = jest.fn();
});
beforeEach(() => {
  jest.clearAllMocks();
  m(Files.listFiles).mockImplementation(() => ok(LIST));
  m(Files.listFileLines).mockImplementation(() => ok(LINES));
});

describe("the Files tab", () => {
  it("lists the primary file and the others, with what each is", async () => {
    show();
    const primary = await screen.findByRole("region", { name: "Primary file" });
    expect(within(primary).getByText("interview.mp3")).toBeInTheDocument();
    expect(within(primary).getByText("Primary · Audio · 3.0 MB")).toBeInTheDocument();
    expect(within(primary).getByRole("link", { name: "Download interview.mp3" })).toHaveAttribute(
      "href",
      LIST.primary!.download,
    );
    const list = screen.getByRole("region", { name: "Supplementary files" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);
    expect(within(list).getByRole("link", { name: "English captions" })).toHaveAttribute("download", "harbour.vtt");
    expect(within(list).getByText("Captions · English · 2.0 KB · 2 timed lines")).toBeInTheDocument();
    expect(within(list).getByText("From the BBC.")).toBeInTheDocument();
    expect(within(list).getAllByText(/Added by Ed Editor/)).toHaveLength(2);
    expect(within(list).getAllByText("Public")).toHaveLength(1);
  });

  it("adds a file, guessing what it is", async () => {
    show();
    await screen.findByRole("region", { name: "Supplementary files" });
    fireEvent.click(screen.getByRole("button", { name: "Add file" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a file" });
    // a picture can't be captions
    picked.push(new File(["x"], "cover.png", { type: "image/png" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Choose a file…" }));
    expect(await within(dialog).findByText("cover.png")).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText("What it is"), { target: { value: "captions" } });
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Captions files are .srt .vtt; this is .png.");

    const vtt = new File(["WEBVTT\n\n00:01.000 --> 00:02.000\nHi\n"], "harbour.vtt", { type: "text/vtt" });
    picked.push(vtt);
    fireEvent.click(within(dialog).getByRole("button", { name: "Another file…" }));
    await within(dialog).findByText("harbour.vtt");
    expect(within(dialog).getByLabelText("What it is")).toHaveValue("captions");
    fireEvent.change(within(dialog).getByLabelText(/Language/), { target: { value: "English" } });
    expect(within(dialog).getByText("Use a language code such as en or pt-BR.")).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText(/Language/), { target: { value: "en" } });
    fireEvent.change(within(dialog).getByLabelText(/Label/), { target: { value: " English captions " } });
    m(Files.addFile).mockImplementation(() => ok(file({ id: 9 })));
    fireEvent.click(within(dialog).getByRole("button", { name: "Add file" }));
    await waitFor(() => expect(m(Files.addFile)).toHaveBeenCalledTimes(1));
    const sent = m(Files.addFile).mock.calls[0][0];
    expect(sent.path).toEqual({ rid: 7 });
    expect(sent.query).toEqual({ role: "captions", name: "harbour.vtt", language: "en", label: "English captions" });
    expect(sent.body).toBe(vtt); // the file itself, as the request's body
    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(expect.objectContaining({ title: "Added English captions" })),
    );
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Add a file" })).not.toBeInTheDocument());
  });

  it("shows a file's lines, which play from their times", async () => {
    const { onSeek } = show();
    await screen.findByRole("region", { name: "Supplementary files" });
    await choose("English captions", "Show its lines");
    const lines = await screen.findByRole("list", { name: "Lines of English captions" });
    expect(within(lines).getByText("Fishing boats come in.")).toBeInTheDocument();
    expect(within(lines).getByText("Ann:")).toBeInTheDocument();
    fireEvent.click(within(lines).getByRole("button", { name: "Play from 0:04–0:06" }));
    expect(onSeek).toHaveBeenCalledWith(4000, true);
    expect(m(Files.listFileLines).mock.calls[0][0]).toMatchObject({ path: { rid: 7, fid: 3 }, query: { offset: 0 } });
  });

  it("opens on a file and line it was linked to", async () => {
    show({ fileFocus: { file: 3, line: 1 } });
    const lines = await screen.findByRole("list", { name: "Lines of English captions" });
    await waitFor(() => expect(within(lines).getByText("Fishing boats come in.").closest("li")).toHaveClass("bg-hl"));
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
  });

  it("changes a file's details and deletes one", async () => {
    show();
    await screen.findByRole("region", { name: "Supplementary files" });
    await choose("English captions", "Edit details…");
    const dialog = await screen.findByRole("dialog", { name: "Edit English captions" });
    expect(within(dialog).getByRole("button", { name: "Save" })).toHaveAttribute("aria-disabled", "true");
    fireEvent.change(within(dialog).getByLabelText("What it is"), { target: { value: "thumbnail" } });
    expect(
      within(dialog).getByText("Thumbnail files are .gif .jpeg .jpg .png .webp; this is .vtt."),
    ).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText("What it is"), { target: { value: "captions" } });
    fireEvent.change(within(dialog).getByLabelText(/Label/), { target: { value: "" } });
    fireEvent.change(within(dialog).getByLabelText(/Description/), { target: { value: "Made by hand." } });
    m(Files.updateFile).mockImplementation(() => ok(file({ label: null })));
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(m(Files.updateFile).mock.calls[0][0]).toEqual(
        expect.objectContaining({ path: { rid: 7, fid: 3 }, body: { label: null, description: "Made by hand." } }),
      ),
    );

    await choose("release.pdf", "Delete…");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Delete release.pdf?");
    m(Files.deleteFile).mockImplementation(() => ok({ ok: true }));
    fireEvent.click(within(alert).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(m(Files.deleteFile).mock.calls[0][0]).toMatchObject({ path: { rid: 7, fid: 4 } }));
    await waitFor(() => expect(toast).toHaveBeenCalledWith(expect.objectContaining({ title: "File deleted" })));
  });

  it("lets viewers download but says who may change files", async () => {
    show({ canEdit: false });
    const add = await screen.findByRole("button", { name: "Add file" });
    expect(add).toHaveAttribute("aria-disabled", "true");
    fireEvent.keyDown(await screen.findByRole("button", { name: "Actions for release.pdf" }), { key: "Enter" });
    const items = await screen.findAllByRole("menuitem");
    expect(items.map((i) => i.textContent)).toEqual([
      "Download",
      "Editors of pods can do this",
      "Editors of pods can do this",
    ]);
  });

  it("says when there are no other files, or they can't load", async () => {
    m(Files.listFiles).mockImplementation(() => ok({ ...LIST, primary: null, files: [] }));
    show();
    expect(await screen.findByText("No other files yet")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Primary file" })).not.toBeInTheDocument();
  });
});
