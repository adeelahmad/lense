import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Chats } from "@/app/openapi-client";
import { PageChat, SharedFrom } from "@/components/page-chat/page-chat";
import {
  hiddenOn,
  PAGE_CHAT_KEY,
  pageContext,
  pageTitle,
  quotePreview,
  readState,
  selectionAllowed,
  tidyText,
} from "@/components/page-chat/page-chat-model";
import { TooltipProvider } from "@/components/ui/tooltip";
import { streamSSE } from "@/lib/api/sse";

jest.mock("@/app/openapi-client", () => ({
  Chats: {
    createChat: jest.fn(),
    getChat: jest.fn(),
    chatCapabilities: jest.fn(),
    stopAnswer: jest.fn(),
  },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
let path = "/library";
jest.mock("next/navigation", () => ({
  usePathname: () => path,
  useSearchParams: () => new URLSearchParams(path === "/library" ? "ns=calls" : ""),
}));
jest.mock("@/lib/api/sse", () => {
  const actual = jest.requireActual("@/lib/api/sse");
  return { ...actual, streamSSE: jest.fn() };
});

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const m = (f: unknown) => f as jest.Mock;

describe("page chat model", () => {
  it("reads what's kept between pages, ignoring anything odd", () => {
    expect(readState(null)).toEqual({ chatId: null, open: false, sharePage: true });
    expect(readState("{nope")).toEqual({ chatId: null, open: false, sharePage: true });
    expect(readState('{"chatId": 7, "open": true, "sharePage": false}')).toEqual({
      chatId: 7,
      open: true,
      sharePage: false,
    });
    expect(readState('{"chatId": -2, "open": "yes"}')).toEqual({ chatId: null, open: false, sharePage: true });
  });

  it("stays out of Chat, which is the full chat", () => {
    expect([hiddenOn("/chat"), hiddenOn("/chat/12"), hiddenOn("/chats-report"), hiddenOn("/library")]).toEqual([
      true,
      true,
      false,
      false,
    ]);
  });

  it("tidies the page's text and cuts it to size", () => {
    expect(tidyText("  A \t b c \n\n\n\n  d  ", 100)).toBe("A b c\n\nd");
    expect(tidyText("abcdef", 3)).toBe("abc");
    expect(pageTitle("Library · Lens Archive")).toBe("Library");
    expect(pageTitle("Lens Archive")).toBe("Lens Archive");
  });

  it("builds what goes with a question", () => {
    expect(pageContext({ url: "/library", title: " Library ", text: "  ", selection: null })).toEqual({
      url: "/library",
      title: "Library",
    });
    expect(pageContext({ url: "/x", title: "", text: "Hello\n\n\nworld", selection: " Alice " })).toEqual({
      url: "/x",
      text: "Hello\n\nworld",
      selection: "Alice",
    });
    expect(pageContext({ url: "/x", title: "T", text: "y".repeat(20000) }).text).toHaveLength(12000);
  });

  it("shortens a quote from both ends", () => {
    expect(quotePreview("short one")).toBe("short one");
    const q = quotePreview(`${"a".repeat(100)} middle ${"z".repeat(100)}`, 41);
    expect(q).toBe(`${"a".repeat(19)} … ${"z".repeat(19)}`);
  });

  it("offers selections from the page, not from the chat, fields or text with its own toolbar", () => {
    document.body.innerHTML = `<main><p id="p">Words</p><div data-own-selection><p id="t">Transcript</p></div>
      <aside data-page-chat><p id="c">Chat</p></aside><textarea id="f"></textarea></main>`;
    const at = (id: string) => document.getElementById(id)!.firstChild ?? document.getElementById(id);
    expect(selectionAllowed("Words", at("p"))).toBe(true);
    expect(selectionAllowed("W", at("p"))).toBe(false);
    expect(selectionAllowed("Transcript", at("t"))).toBe(false);
    expect(selectionAllowed("Chat", at("c"))).toBe(false);
    expect(selectionAllowed("typed", at("f"))).toBe(false);
    expect(selectionAllowed("Words", null)).toBe(false);
  });
});

describe("SharedFrom", () => {
  it("links back to the page and shows the quote", () => {
    render(<SharedFrom context={{ url: "/resources/4", title: "Ep 4", selection: "We ship Friday", page: true }} />);
    expect(screen.getByRole("link", { name: /Shared Ep 4/ })).toHaveAttribute("href", "/resources/4");
    expect(screen.getByText("“We ship Friday”")).toBeInTheDocument();
  });
  it("shows nothing for a question asked in Chat", () => {
    const { container } = render(<SharedFrom context={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});

function renderChat() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <main id="main">
          <h1>Library page</h1>
          <p>Recordings from the calls namespace.</p>
        </main>
        <PageChat />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

describe("PageChat", () => {
  beforeEach(() => {
    path = "/library";
    localStorage.clear();
    document.title = "Library · Lens Archive";
    m(Chats.chatCapabilities).mockReturnValue(
      ok({ configured: true, model: "m", tools: false, max_steps: 6, check: true, models: ["m"] }),
    );
    m(Chats.getChat).mockReturnValue(ok({ id: 9, title: "What is here?", account: 1, scope: {}, messages: [] }));
  });

  it("asks with the page and keeps the conversation until New chat", async () => {
    m(Chats.createChat).mockReturnValue(ok({ id: 9 }));
    const sent: unknown[] = [];
    m(streamSSE).mockImplementation(async function* (_url: string, opts: { body: unknown }) {
      sent.push(opts.body);
      yield { event: "passages", data: "[]" };
      yield { event: "token", data: JSON.stringify({ text: "It lists recordings." }) };
      yield { event: "done", data: JSON.stringify({ message: 2 }) };
    });
    renderChat();
    fireEvent.click(screen.getByRole("button", { name: "Chat about this page" }));
    expect(await screen.findByRole("complementary", { name: "Page chat" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Library" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.change(screen.getByRole("textbox", { name: "Ask a question" }), { target: { value: "What is here?" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(m(Chats.createChat).mock.calls[0][0].body).toEqual({});
    expect(sent[0]).toEqual({
      content: "What is here?",
      context: {
        url: "/library?ns=calls",
        title: "Library",
        text: expect.stringContaining("Recordings from the calls namespace."),
      },
    });
    await waitFor(() =>
      expect(JSON.parse(localStorage.getItem(PAGE_CHAT_KEY)!)).toMatchObject({ chatId: 9, open: true }),
    );
    expect(screen.getByRole("link", { name: "Open in Chat" })).toHaveAttribute("href", "/chat/9");

    fireEvent.click(screen.getByRole("button", { name: /New chat/ }));
    expect(JSON.parse(localStorage.getItem(PAGE_CHAT_KEY)!)).toMatchObject({ chatId: null, open: true });
    expect(screen.getByText("Ask about this page, or anything in Lens")).toBeInTheDocument();
  });

  it("picks the conversation up again on another page, and can stop sharing the page", async () => {
    localStorage.setItem(PAGE_CHAT_KEY, JSON.stringify({ chatId: 9, open: true, sharePage: true }));
    m(Chats.getChat).mockReturnValue(
      ok({
        id: 9,
        title: "Earlier",
        account: 1,
        scope: {},
        messages: [
          {
            id: 1,
            role: "user",
            content: "Who is Alice?",
            context: { url: "/speakers", title: "Speakers", selection: "Alice", page: true },
          },
          { id: 2, role: "assistant", content: "A host.", passages: [], steps: [], stopped: false },
        ],
      }),
    );
    const sent: unknown[] = [];
    m(streamSSE).mockImplementation(async function* (_url: string, opts: { body: unknown }) {
      sent.push(opts.body);
      yield { event: "done", data: JSON.stringify({ message: 4 }) };
    });
    renderChat();
    expect(await screen.findByText("A host.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Shared Speakers/ })).toHaveAttribute("href", "/speakers");
    fireEvent.click(screen.getByRole("button", { name: "Library" }));
    expect(screen.getByRole("button", { name: "Library" })).toHaveAttribute("aria-pressed", "false");
    fireEvent.change(screen.getByRole("textbox", { name: "Ask a question" }), { target: { value: "More?" } });
    await act(async () => {
      fireEvent.keyDown(screen.getByRole("textbox", { name: "Ask a question" }), { key: "Enter" });
    });
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(Chats.createChat).not.toHaveBeenCalled();
    expect(sent[0]).toEqual({ content: "More?", context: { url: "/library?ns=calls", title: "Library" } });
  });

  it("isn't on the Chat page", () => {
    path = "/chat/3";
    renderChat();
    expect(screen.queryByRole("button", { name: "Chat about this page" })).not.toBeInTheDocument();
  });
});
