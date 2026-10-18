import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Chats } from "@/app/openapi-client";
import { answerMarkdown, plainText } from "@/components/chat/copy";
import { AnswerMenu, exportName, ExportMenu, useChatExport } from "@/components/chat/export";
import { parseBlocks, RichText } from "@/components/chat/rich-text";
import { UserBubble } from "@/components/chat/user-bubble";
import { saveBlob } from "@/components/recording/hooks";
import { ToastProvider } from "@/components/ui/toast";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({ Chats: { exportChat: jest.fn() } }));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("@/components/recording/hooks", () => ({
  ...jest.requireActual("@/components/recording/hooks"),
  saveBlob: jest.fn(),
}));
jest.mock("@/components/chat/attachments", () => ({ SentFiles: () => null }));

const ORIGIN = window.location.origin;
const PASSAGES = [
  {
    n: 1,
    recording_id: 5,
    title: "Episode one",
    t0: 75_000,
    time: "1:15",
    speaker: "Ana",
    text: "The ship\nleaves Friday.",
  },
  { n: 2, recording_id: 7, title: "The call", t0: 0, time: "0:00", speaker: null, text: "It's late." },
  { n: 3, recording_id: 8, title: "Not cited", t0: 0, time: "0:00", speaker: null, text: "x" },
  {
    n: 4,
    source: "web",
    url: "https://example.org/ships",
    title: "Ships",
    site: "example.org",
    recording_id: null,
    text: "Ships are slow.",
  },
];
const ANSWER =
  "It leaves **on Friday** [1], late [2, 9]. Ships are slow [4]. See [the plan](https://example.org/plan).";

let writeText: jest.Mock;
beforeEach(() => {
  jest.clearAllMocks();
  writeText = jest.fn(() => Promise.resolve());
  Object.assign(navigator, { clipboard: { writeText } });
});

function show(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <ToastProvider>{ui}</ToastProvider>
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

async function choose(button: string, item: string) {
  fireEvent.keyDown(screen.getByRole("button", { name: button }), { key: "Enter" });
  fireEvent.click(await screen.findByRole("menuitem", { name: item }));
}

describe("copying an answer", () => {
  it("gives Markdown with citations as footnotes linking back to Lens, or plain text", () => {
    expect(answerMarkdown(ANSWER, PASSAGES, ORIGIN)).toBe(
      "It leaves **on Friday** [^1], late [^2][9]. Ships are slow [^4]. See [the plan](https://example.org/plan).\n\n" +
        `[^1]: Episode one, 1:15, Ana: “The ship leaves Friday.” <${ORIGIN}/resources/5?t=75>\n` +
        `[^2]: The call, 0:00: “It's late.” <${ORIGIN}/resources/7>\n` +
        "[^4]: [Ships](https://example.org/ships), example.org: “Ships are slow.”\n",
    );
    expect(answerMarkdown("No citations here.", [], ORIGIN)).toBe("No citations here.\n");
    expect(plainText(ANSWER)).toBe(
      "It leaves on Friday, late. Ships are slow. See the plan (https://example.org/plan).",
    );
    expect(plainText("## Steps\n\n```bash\nls\n```")).toBe("Steps\n\nls");
  });

  it("copies as Markdown in one click, and the rest from the arrow beside it", async () => {
    const onExport = jest.fn();
    show(<AnswerMenu text={ANSWER} passages={PASSAGES} onExport={onExport} />);
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(answerMarkdown(ANSWER, PASSAGES, ORIGIN)));
    expect(await screen.findByText("Copied as Markdown")).toBeInTheDocument();

    const more = "More ways to copy and export";
    await choose(more, "Copy text");
    expect(writeText).toHaveBeenLastCalledWith(plainText(ANSWER));

    await choose(more, "Copy as Markdown");
    expect(writeText).toHaveBeenLastCalledWith(answerMarkdown(ANSWER, PASSAGES, ORIGIN));

    await choose(more, "PDF");
    await waitFor(() => expect(onExport).toHaveBeenCalledWith("pdf"));
  });

  it("says so when the browser won't copy", async () => {
    writeText.mockImplementation(() => Promise.reject(new Error("denied")));
    show(<AnswerMenu text="Hi" passages={[]} />);
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(await screen.findByText("Couldn’t copy")).toBeInTheDocument();
    // not saved yet: nothing to export
    fireEvent.keyDown(screen.getByRole("button", { name: "More ways to copy" }), { key: "Enter" });
    expect(await screen.findByRole("menuitem", { name: "Copy text" })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "PDF" })).not.toBeInTheDocument();
  });

  it("copies a question", async () => {
    show(<UserBubble text="When does it leave?" />);
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("When does it leave?"));
  });
});

describe("code blocks", () => {
  it("parses fences, keeping the code as written", () => {
    expect(parseBlocks("Run:\n\n```bash\nls -la\n  cd x\n```\nDone [1].")).toEqual([
      { type: "p", text: "Run:" },
      { type: "code", lang: "bash", text: "ls -la\n  cd x" },
      { type: "p", text: "Done [1]." },
    ]);
    // one still being written ends with the text
    expect(parseBlocks("~~~\n# not a heading\n- nor a list")).toEqual([
      { type: "code", lang: "", text: "# not a heading\n- nor a list" },
    ]);
  });

  it("copies a code block and says Copied", async () => {
    show(<RichText text={"Try:\n```python\nprint('hi')\n```"} renderCite={() => null} />);
    expect(screen.getByLabelText("python code")).toHaveTextContent("print('hi')");
    fireEvent.click(screen.getByRole("button", { name: "Copy code" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("print('hi')"));
    expect(await screen.findByText("Copied")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copied" })).toBeInTheDocument();
  });
});

function Exporter({ onPrint }: { onPrint: (message?: number) => void }) {
  const ex = useChatExport(4, "Shipping questions", onPrint);
  return (
    <>
      <ExportMenu label="Export conversation" onExport={(fmt) => ex.mutate({ fmt })} />
      <button type="button" onClick={() => ex.mutate({ fmt: "pdf", message: 9 })}>
        one
      </button>
    </>
  );
}

describe("exporting", () => {
  it("downloads what the server makes, named as the server names it", async () => {
    const blob = new Blob(["# x"]);
    (Chats.exportChat as jest.Mock).mockResolvedValue({ data: blob, response: { ok: true, status: 200 } });
    show(<Exporter onPrint={jest.fn()} />);
    await choose("Export conversation", "Markdown");
    await waitFor(() => expect(saveBlob).toHaveBeenCalledWith(blob, "shipping-questions.md"));
    expect(Chats.exportChat).toHaveBeenCalledWith(
      expect.objectContaining({ path: { cid: 4, fmt: "md" }, query: undefined, parseAs: "blob" }),
    );
    expect(exportName("Shipping questions", "json", 9)).toBe("shipping-questions-message-9.json");
    expect(exportName(null, "pdf")).toBe("conversation.pdf");
  });

  it("prints in the browser when the server can't make the PDF", async () => {
    (Chats.exportChat as jest.Mock).mockResolvedValue({
      error: { detail: "a PDF needs Chromium, anytopdf or LibreOffice on the server" },
      response: { ok: false, status: 409 },
    });
    const onPrint = jest.fn();
    show(<Exporter onPrint={onPrint} />);
    fireEvent.click(screen.getByRole("button", { name: "one" }));
    await waitFor(() => expect(onPrint).toHaveBeenCalledWith(9));
    expect(await screen.findByText("Printing it instead")).toBeInTheDocument();
    expect(saveBlob).not.toHaveBeenCalled();
  });

  it("says what went wrong otherwise", async () => {
    (Chats.exportChat as jest.Mock).mockResolvedValue({
      error: { detail: "no such message in this conversation" },
      response: { ok: false, status: 404 },
    });
    const onPrint = jest.fn();
    show(<Exporter onPrint={onPrint} />);
    await choose("Export conversation", "JSON");
    expect(await screen.findByText("no such message in this conversation")).toBeInTheDocument();
    expect(onPrint).not.toHaveBeenCalled();
  });
});
