import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import type { Chat, ChatMessage, ChatNode } from "@/app/openapi-client/types.gen";
import { Answer } from "@/components/chat/answer";
import { lineage, newestBelow, switched, versionsOf, VersionSwitch } from "@/components/chat/versions";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({ Chats: { checkMessage: jest.fn() }, Search: {} }));
jest.mock("@/lib/api/browser", () => ({ useApiClient: () => ({}), data: jest.fn() }));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("@/lib/hooks/session", () => ({
  useArchive: () => ({ can: () => true, namespaces: [], roleIn: () => "editor" }),
}));

// a saved answer's Details opens as a sheet on narrow screens, so it asks the screen's width
beforeAll(() => {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  })) as unknown as typeof window.matchMedia;
});

// q1 → a1 → q2 → a2, a1 regenerated as b1 (→ q3 → a3), and q1 edited as e1 (→ f1)
const node = (id: number, parent: number | null, role: "user" | "assistant"): ChatNode => ({ id, parent, role });
const TREE = [
  node(1, null, "user"),
  node(2, 1, "assistant"),
  node(3, 2, "user"),
  node(4, 3, "assistant"),
  node(5, 1, "assistant"),
  node(6, 5, "user"),
  node(7, 6, "assistant"),
  node(8, null, "user"),
  node(9, 8, "assistant"),
];
const said = (id: number, role: "user" | "assistant" = id % 2 ? "user" : "assistant"): ChatMessage => ({
  id,
  role,
  content: `m${id}`,
});

describe("versions of a conversation", () => {
  it("groups versions by what they follow and who said them, oldest first", () => {
    const v = versionsOf(TREE);
    expect(v.get(5)).toEqual({ index: 2, count: 2, ids: [2, 5] });
    expect(v.get(8)).toEqual({ index: 2, count: 2, ids: [1, 8] });
    expect(v.get(3)).toEqual({ index: 1, count: 1, ids: [3] });
  });

  it("shows a version to its newest message", () => {
    expect(newestBelow(TREE, 2)).toBe(4);
    expect(newestBelow(TREE, 1)).toBe(7);
    expect(newestBelow(TREE, 9)).toBe(9);
    expect(lineage(TREE, 7)).toEqual([1, 5, 6, 7]);
    expect(lineage(TREE, 99)).toEqual([]);
  });

  it("switches at once to a version already seen, and waits for one that isn't", () => {
    const chat = {
      id: 1,
      title: "t",
      account: 1,
      head: 4,
      tree: TREE,
      messages: [1, 2, 3, 4].map((i) => said(i)),
    } as Chat;
    const seen = new Map([1, 2, 3, 4, 5].map((i) => [i, said(i, i === 5 ? "assistant" : undefined)]));
    expect(switched(chat, 5, seen)).toBeNull(); // 6 and 7 haven't been loaded
    for (const i of [6, 7]) seen.set(i, said(i));
    const out = switched(chat, 5, seen)!;
    expect(out.head).toBe(7);
    expect(out.messages!.map((m) => m.id)).toEqual([1, 5, 6, 7]);
    expect(out.messages![1].versions).toEqual({ index: 2, count: 2, ids: [2, 5] });
  });
});

describe("the version switch", () => {
  const wrap = (ui: React.ReactNode) => render(<TooltipProvider>{ui}</TooltipProvider>);

  it("moves between versions with its arrows and the arrow keys, and stops at either end", () => {
    const onPick = jest.fn();
    wrap(<VersionSwitch versions={{ index: 2, count: 3, ids: [2, 5, 10] }} what="answer" onPick={onPick} />);
    expect(screen.getByRole("group", { name: "Versions of this answer" })).toHaveTextContent("2 / 3");
    fireEvent.click(screen.getByRole("button", { name: "Previous version of this answer" }));
    fireEvent.click(screen.getByRole("button", { name: "Next version of this answer" }));
    fireEvent.keyDown(screen.getByRole("button", { name: "Next version of this answer" }), { key: "ArrowLeft" });
    expect(onPick.mock.calls).toEqual([[2], [10], [2]]);
  });

  it("doesn't go past the first or last", () => {
    const onPick = jest.fn();
    wrap(<VersionSwitch versions={{ index: 1, count: 2, ids: [1, 8] }} what="question" onPick={onPick} />);
    const back = screen.getByRole("button", { name: "Previous version of this question" });
    expect(back).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(back);
    fireEvent.keyDown(back, { key: "ArrowLeft" });
    expect(onPick).not.toHaveBeenCalled();
    fireEvent.keyDown(back, { key: "ArrowRight" });
    expect(onPick).toHaveBeenCalledWith(8);
  });

  it("shows nothing for a message with one version", () => {
    const { container } = wrap(<VersionSwitch versions={{ index: 1, count: 1, ids: [3] }} what="answer" />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("regenerating an answer", () => {
  const answer = (onRegenerate?: (m?: string) => void) =>
    render(
      <QueryClientProvider client={new QueryClient()}>
        <TooltipProvider>
          <Answer
            chatId={1}
            messageId={2}
            question="When does it ship?"
            text="On Friday."
            passages={[]}
            status="done"
            steps={[{ tool: "search_transcripts", args: {}, summary: "Searched" }]}
            scope={{}}
            model="fake"
            models={["fake", "fake-large"]}
            onRegenerate={onRegenerate}
            hover={null}
            onHover={() => {}}
            onPreview={() => {}}
          />
        </TooltipProvider>
      </QueryClientProvider>,
    );

  it("writes a new answer with the same model, or another one", async () => {
    const onRegenerate = jest.fn();
    answer(onRegenerate);
    fireEvent.click(screen.getByRole("button", { name: "Regenerate" }));
    expect(onRegenerate).toHaveBeenLastCalledWith();
    fireEvent.keyDown(screen.getByRole("button", { name: "Regenerate with another model" }), { key: "Enter" });
    expect(screen.queryByRole("menuitem", { name: "fake" })).not.toBeInTheDocument(); // it wrote this one
    fireEvent.click(await screen.findByRole("menuitem", { name: "fake-large" }));
    await waitFor(() => expect(onRegenerate).toHaveBeenLastCalledWith("fake-large")); // a menu acts once it has closed
  });

  it("isn't offered without a handler, e.g. while another answer is written", () => {
    answer();
    expect(screen.queryByRole("button", { name: "Regenerate" })).not.toBeInTheDocument();
  });
});
