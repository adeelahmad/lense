import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom";
import type { ReactNode } from "react";

import { Replies } from "@/app/openapi-client";
import type { Reply, ReplyEmail } from "@/app/openapi-client/types.gen";
import { NAV } from "@/components/app-shell/nav-config";
import { addresses, isEmail, mailtoHref, plainText, sourceHref, unconfirmed } from "@/components/replies/model";
import { RepliesPage, UNDO_MS } from "@/components/replies/replies-page";
import { ToastProvider } from "@/components/ui/toast";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Replies: {
    listReplies: jest.fn(),
    getReply: jest.fn(),
    draftReply: jest.fn(),
    editReply: jest.fn(),
    sendReply: jest.fn(),
  },
}));
let search = "";
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
  usePathname: () => "/replies",
  useSearchParams: () => new URLSearchParams(search),
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
let editor = true;
let admin = false;
jest.mock("@/lib/hooks/session", () => ({
  ...jest.requireActual("@/lib/hooks/session"),
  useArchive: () => ({ namespace: null, can: () => editor, admin }),
}));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const m = (f: unknown) => f as jest.Mock;

const DRAFT: Reply = {
  status: "draft",
  to: ["mara@example.org"],
  cc: [],
  others: ["tom@example.org"],
  from: "me@example.org",
  subject: "Re: Harbour report",
  text: "Hi Mara,\n\nThe ships arrived at dawn [1]. We can meet on [to confirm: the date].",
  gaps: ["the date to meet"],
  unconfirmed: 1,
  sources: [
    {
      n: 1,
      recording_id: 3,
      title: "keeper's log",
      time: "0:01",
      t0: 1000,
      text: "The ships arrived at dawn.",
      used: true,
    },
  ],
  drafted_at: "2026-10-18T06:00:00Z",
};

function email(id: number, reply: Reply = { status: "none" }, over: Partial<ReplyEmail> = {}): ReplyEmail {
  return {
    recording: id,
    subject: `Harbour report ${id}`,
    sender: "Mara <mara@example.org>",
    from: "mara@example.org",
    namespace: "pods",
    received_at: "2026-10-17T10:00:00Z",
    snippet: "When did the ships arrive?",
    reply,
    ...over,
  };
}

let emails: ReplyEmail[] = [];
function serve() {
  m(Replies.listReplies).mockImplementation(() =>
    ok({ emails, drafts: emails.filter((e) => e.reply.status === "draft").length }),
  );
  m(Replies.draftReply).mockImplementation(({ path }: { path: { rid: number } }) => {
    emails = emails.map((e) => (e.recording === path.rid ? { ...e, reply: { status: "drafting", job: 9 } } : e));
    return ok({ job: 9 });
  });
  m(Replies.editReply).mockImplementation(
    ({ path, body }: { path: { rid: number }; body: Record<string, unknown> }) => {
      emails = emails.map((e) =>
        e.recording === path.rid
          ? { ...e, reply: { ...e.reply, ...body, updated_at: new Date().toISOString() } as Reply }
          : e,
      );
      return ok(emails.find((e) => e.recording === path.rid)?.reply);
    },
  );
  m(Replies.sendReply).mockImplementation(({ path, body }: { path: { rid: number }; body: { draft: boolean } }) => {
    const sent = { at: new Date().toISOString(), by: "root@x.io", url: "https://mail.google.com/mail/u/me/#all/m1" };
    emails = emails.map((e) =>
      e.recording === path.rid ? { ...e, reply: { ...e.reply, status: body.draft ? "saved" : "sent", sent } } : e,
    );
    return ok(emails.find((e) => e.recording === path.rid)?.reply);
  });
}

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
  editor = true;
  admin = false;
  emails = [email(1), email(2, DRAFT)];
  jest.clearAllMocks();
  serve();
});

describe("the replies page", () => {
  it("drafts a reply from the first field with Enter, saying what it should say", async () => {
    wrap(<RepliesPage />);
    const say = await screen.findByRole("textbox", { name: "What the reply should say" });
    expect(say).toHaveFocus();
    expect(screen.getByText("2 emails wait for an answer, 1 draft ready")).toBeInTheDocument();
    fireEvent.change(say, { target: { value: "Say Tuesday works." } });
    fireEvent.submit(say.closest("form") as HTMLFormElement);
    await waitFor(() =>
      expect(Replies.draftReply).toHaveBeenCalledWith(
        expect.objectContaining({ path: { rid: 1 }, body: { instructions: "Say Tuesday works." } }),
      ),
    );
    expect(await screen.findByText("Drafting a reply from your archive…")).toBeInTheDocument();
  });

  it("shows a draft with its sources and what's left to confirm, and opens it in your mail app", async () => {
    wrap(<RepliesPage />);
    const card = await screen.findByRole("article", { name: "Harbour report 2" });
    expect(within(card).getByText("Draft ready, 1 to confirm")).toBeInTheDocument();
    expect(within(card).getByRole("link", { name: "keeper's log, 0:01" })).toHaveAttribute("href", "/resources/3?t=1");
    expect(within(card).getByText(/the archive doesn’t say the date to meet/)).toBeInTheDocument();
    const open = within(card).getByRole("link", { name: "Open in your mail app" });
    expect(open.getAttribute("href")).toMatch(/^mailto:mara%40example\.org\?subject=Re%3A%20Harbour%20report&body=/);
    expect(decodeURIComponent(open.getAttribute("href") as string)).not.toContain("[1]");
    // reply to all puts the email's other recipients in Cc
    fireEvent.click(within(card).getByRole("button", { name: "Reply to all" }));
    expect(within(card).getByLabelText("Cc")).toHaveValue("tom@example.org");
  });

  it("saves an edit with Ctrl+Enter, and won't save an address that isn't one", async () => {
    wrap(<RepliesPage />);
    const card = await screen.findByRole("article", { name: "Harbour report 2" });
    fireEvent.change(within(card).getByLabelText("To"), { target: { value: "not-an-address" } });
    expect(within(card).getByRole("alert")).toHaveTextContent("“not-an-address” isn’t an email address.");
    expect(within(card).getByRole("button", { name: "Save" })).toBeDisabled();
    fireEvent.change(within(card).getByLabelText("To"), { target: { value: "mara@example.org" } });
    const text = within(card).getByRole("textbox", { name: "The reply" });
    fireEvent.change(text, { target: { value: "Hi Mara, Tuesday it is [1]." } });
    fireEvent.keyDown(text, { key: "Enter", ctrlKey: true });
    await waitFor(() =>
      expect(Replies.editReply).toHaveBeenCalledWith(
        expect.objectContaining({
          path: { rid: 2 },
          body: {
            text: "Hi Mara, Tuesday it is [1].",
            subject: "Re: Harbour report",
            to: ["mara@example.org"],
            cc: [],
          },
        }),
      ),
    );
    expect(await screen.findByText("Draft saved")).toBeInTheDocument();
  });

  it("sets a draft aside at once, with Undo instead of a question", async () => {
    wrap(<RepliesPage />);
    const card = await screen.findByRole("article", { name: "Harbour report 2" });
    fireEvent.click(within(card).getByRole("button", { name: "Set aside" }));
    await waitFor(() => expect(screen.queryByRole("article", { name: "Harbour report 2" })).not.toBeInTheDocument());
    expect(Replies.editReply).toHaveBeenCalledWith(expect.objectContaining({ body: { status: "dismissed" } }));
    fireEvent.click(await screen.findByRole("button", { name: "Undo" }));
    await waitFor(() =>
      expect(Replies.editReply).toHaveBeenLastCalledWith(expect.objectContaining({ body: { status: "draft" } })),
    );
    expect(await screen.findByRole("article", { name: "Harbour report 2" })).toBeInTheDocument();
  });

  it("sends through Gmail after a few seconds unless undone, once nothing is left to confirm", async () => {
    admin = true;
    const ready = { ...DRAFT, sends: true, text: "Hi Mara, the ships arrived at dawn [1].", unconfirmed: 0 };
    emails = [email(2, { ...DRAFT, sends: true }), email(3, ready)];
    jest.useFakeTimers();
    try {
      wrap(<RepliesPage />);
      const waiting = await screen.findByRole("article", { name: "Harbour report 2" });
      expect(within(waiting).getByRole("button", { name: "Send" })).toHaveAttribute("aria-disabled", "true");
      const card = screen.getByRole("article", { name: "Harbour report 3" });
      fireEvent.click(within(card).getByRole("button", { name: "Send" }));
      expect(screen.getByText("Sending “Re: Harbour report”")).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Undo" }));
      act(() => jest.advanceTimersByTime(UNDO_MS + 100));
      expect(Replies.sendReply).not.toHaveBeenCalled(); // taken back: nothing sent
      // an edit not saved yet is saved, then sent
      fireEvent.change(within(card).getByRole("textbox", { name: "The reply" }), {
        target: { value: "Hi Mara, see you Tuesday." },
      });
      fireEvent.click(within(card).getByRole("button", { name: "Send" }));
      act(() => jest.advanceTimersByTime(UNDO_MS + 100));
      await waitFor(() =>
        expect(Replies.sendReply).toHaveBeenCalledWith(
          expect.objectContaining({ path: { rid: 3 }, body: { draft: false } }),
        ),
      );
      expect(Replies.editReply).toHaveBeenCalledWith(
        expect.objectContaining({
          path: { rid: 3 },
          body: expect.objectContaining({ text: "Hi Mara, see you Tuesday." }),
        }),
      );
    } finally {
      jest.useRealTimers();
    }
  });

  it("keeps a draft in Gmail at once, and offers no Send without a connection that sends or to members", async () => {
    admin = true;
    emails = [email(3, { ...DRAFT, sends: true }), email(4, DRAFT)];
    wrap(<RepliesPage />);
    const card = await screen.findByRole("article", { name: "Harbour report 3" });
    fireEvent.click(within(card).getByRole("button", { name: "Keep in Gmail drafts" }));
    await waitFor(() =>
      expect(Replies.sendReply).toHaveBeenCalledWith(
        expect.objectContaining({ path: { rid: 3 }, body: { draft: true } }),
      ),
    );
    expect(await screen.findByText("Kept in Gmail’s drafts")).toBeInTheDocument();
    const plain = screen.getByRole("article", { name: "Harbour report 4" });
    expect(within(plain).queryByRole("button", { name: "Send" })).not.toBeInTheDocument();
  });

  it("brings the email it was sent for first, and viewers read without acting", async () => {
    search = "email=2";
    editor = false;
    wrap(<RepliesPage />);
    const cards = await screen.findAllByRole("article");
    expect(cards[0]).toHaveAccessibleName("Harbour report 2");
    expect(within(cards[0]).getByRole("textbox", { name: "The reply" })).toBeDisabled();
    expect(within(cards[0]).queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
    expect(within(cards[1]).getByRole("button", { name: "Draft a reply" })).toHaveAttribute("aria-disabled", "true");
  });

  it("says where mail comes from when none waits", async () => {
    emails = [];
    wrap(<RepliesPage />);
    expect(await screen.findByText("No email waits for an answer")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Watch a mailbox" })).toHaveAttribute("href", "/sources");
  });
});

describe("the replies model", () => {
  it("strips citations as the backend does, and counts what's left to confirm", () => {
    expect(plainText("Hi [1]. Ships at dawn [2] , fog [3]!")).toBe("Hi. Ships at dawn, fog!");
    expect(unconfirmed("On [to confirm: the date] at [To confirm: the time].")).toBe(2);
  });

  it("reads typed addresses and builds a mail app link", () => {
    expect(addresses("a@x.io, b@y.org;c@z.net")).toEqual({ list: ["a@x.io", "b@y.org", "c@z.net"], wrong: null });
    expect(addresses("a@x.io, nope").wrong).toBe("nope");
    expect(mailtoHref({ to: ["a@x.io"], cc: ["b@y.org"], subject: "Re: Hi", text: "Yes [1]." })).toBe(
      "mailto:a%40x.io?cc=b%40y.org&subject=Re%3A%20Hi&body=Yes.",
    );
  });

  it("links a document's source to its page and anything else to its moment", () => {
    expect(sourceHref({ recording_id: 4, page: 2, t0: 0 })).toBe("/resources/4?page=3");
    expect(sourceHref({ recording_id: 4, page: null, t0: 61000 })).toBe("/resources/4?t=61");
  });

  it("knows an email resource, and the nav has Replies only while email waits", () => {
    expect(isEmail({ email: { subject: "Hi" } })).toBe(true);
    expect(isEmail({ path: "in/hello.EML" })).toBe(true);
    expect(isEmail({ path: "talk.mp3" })).toBe(false);
    expect(NAV.find((i) => i.href === "/replies")).toMatchObject({ countKey: "replies", whileCounted: true });
  });
});
