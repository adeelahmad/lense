import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Activity, Chats } from "@/app/openapi-client";
import type { MessageRecord } from "@/app/openapi-client/types.gen";
import { AnswerDetails, DetailsBody } from "@/components/chat/details";
import { took } from "@/components/chat/steps";
import { applyEvent, newTurn, savedSteps } from "@/components/chat/stream";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Chats: { getMessage: jest.fn() },
  Activity: { resourceCosts: jest.fn() },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });

const META: MessageRecord = {
  model: "qwen3:8b",
  provider: "ollama.lan",
  tools: { picked: null, offered: ["search_transcripts", "read_transcript", "web_read"] },
  scope: { namespaces: ["pods"] },
  limits: { rounds: 6, tool_calls: null },
  used: { rounds: 2, tool_calls: 3 },
  outcome: "limit",
  tokens: { in: 5120, out: 410 },
  cost_usd: 0.0031,
  cost_estimated: true,
  ms: 8200,
  flagged: ["web_read"],
  version: "0.13.0",
};

function wide(matches: boolean) {
  window.matchMedia = ((q: string) => ({
    matches: q.includes("max-width") ? !matches : matches,
    addEventListener: () => {},
    removeEventListener: () => {},
  })) as unknown as typeof window.matchMedia;
}

function show(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>{ui}</TooltipProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  wide(true);
  (Chats.getMessage as jest.Mock).mockImplementation(() =>
    ok({
      id: 8,
      role: "assistant",
      content: "x",
      ledger: [
        {
          id: "a1",
          at: "2026-10-16T10:00:00Z",
          kind: "out",
          action: "model.chat",
          model: "qwen3:8b",
          tokens_in: 4000,
          tokens_out: 300,
          ms: 5100,
          ok: true,
        },
        { id: "a2", at: "2026-10-16T10:00:01Z", kind: "in", action: "POST /api/v1/chats/{cid}/messages", ok: true },
      ],
    }),
  );
  (Activity.resourceCosts as jest.Mock).mockImplementation(() =>
    ok({ costs: { "chat:3": { cost_usd: 0.02, tokens: 9000, calls: 4, unpriced: 0, estimate: false } } }),
  );
});

describe("an answer's record", () => {
  it("says what produced it: model and host, scope, tools, use, tokens, cost, time and how it ended", async () => {
    show(
      <DetailsBody
        meta={META}
        chatId={3}
        messageId={8}
        steps={savedSteps([
          { tool: "search_transcripts", args: {}, summary: "Searched", ms: 120 },
          { tool: "web_read", args: {}, summary: "Read", ms: 900 },
        ])}
      />,
    );
    expect(screen.getByText("Its tool rounds ran out, so it answered from what it found")).toBeInTheDocument();
    expect(screen.getByText("qwen3:8b")).toBeInTheDocument();
    expect(screen.getByText(/on ollama\.lan/)).toBeInTheDocument();
    expect(screen.getByText("pods")).toBeInTheDocument();
    expect(screen.getByText("3 tools offered")).toBeInTheDocument();
    expect(screen.getByText(/2 of 6 rounds, 3 tool calls/)).toBeInTheDocument();
    expect(screen.getByText("search_transcripts, web_read")).toBeInTheDocument();
    expect(screen.getByText(/web_read: what it read looked like instructions to an AI/)).toBeInTheDocument();
    expect(screen.getByText("5.1k in · 410 out")).toBeInTheDocument();
    expect(screen.getByText("(estimated from the prices in Settings)")).toBeInTheDocument();
    expect(screen.getByText("≈")).toBeInTheDocument(); // some calls weren't priced: at least this
    expect(screen.getByText("8.2 s")).toBeInTheDocument();
    // the calls made for it, from the ledger (calls out only)
    expect(await screen.findByText(/Model call · qwen3:8b/)).toBeInTheDocument();
    expect(screen.queryByText(/POST \/api\/v1/)).not.toBeInTheDocument();
    expect(Chats.getMessage).toHaveBeenCalledWith(expect.objectContaining({ path: { cid: 3, mid: 8 } }));
    expect(await screen.findByText("$0.02")).toBeInTheDocument(); // the conversation so far
  });

  it("says an old answer wasn't recorded, and asks the ledger nothing", () => {
    show(<DetailsBody meta={null} chatId={3} messageId={8} steps={[]} />);
    expect(screen.getByText(/from before Lens recorded what produced each one/)).toBeInTheDocument();
    expect(Chats.getMessage).not.toHaveBeenCalled();
  });

  it("an answer without tools or a price says so", () => {
    const plain: MessageRecord = {
      ...META,
      tools: { picked: null, offered: [] },
      limits: { rounds: null, tool_calls: null },
      used: { rounds: 0, tool_calls: 0 },
      outcome: "answered",
      cost_usd: 0,
      cost_estimated: false,
      flagged: [],
    };
    show(<DetailsBody meta={plain} chatId={null} messageId={null} steps={[]} />);
    expect(screen.getByText("None offered")).toBeInTheDocument();
    expect(screen.getByText("No tools")).toBeInTheDocument();
    expect(screen.getByText("Not costed: no price is set for its model")).toBeInTheDocument();
    expect(screen.queryByText("Flagged")).not.toBeInTheDocument();
  });

  it("opens from the i in the answer's header, by keyboard too; on a phone in a sheet", async () => {
    show(<AnswerDetails meta={META} chatId={3} messageId={8} steps={[]} />);
    const button = screen.getByRole("button", { name: "Details: what produced this answer" });
    button.focus();
    fireEvent.click(button);
    expect(await screen.findByText("What produced this answer")).toBeInTheDocument();
    expect(screen.getByText("qwen3:8b")).toBeInTheDocument();

    wide(false);
    show(<AnswerDetails meta={null} chatId={3} messageId={9} steps={[]} />);
    fireEvent.click(screen.getAllByRole("button", { name: "Details: what produced this answer" }).at(-1)!);
    expect(await screen.findByRole("dialog")).toHaveTextContent("What produced this answer");
    expect(screen.getByRole("dialog")).toHaveTextContent(/from before Lens recorded/);
  });
});

describe("the record in the stream", () => {
  it("keeps the meta event, and each step's time and failure", () => {
    let s = newTurn("q");
    s = applyEvent(s, {
      event: "step",
      data: '{"tool":"read_transcript","args":{},"summary":"read_transcript: not found","ms":40,"failed":true}',
    });
    s = applyEvent(s, { event: "meta", data: JSON.stringify(META) });
    s = applyEvent(s, { event: "done", data: '{"message":8}' });
    expect(s.steps[0]).toMatchObject({ ms: 40, failed: true });
    expect(s.meta).toMatchObject({ outcome: "limit", provider: "ollama.lan" });
    expect(savedSteps([{ tool: "x" }])[0]).toEqual({ tool: "x", args: {}, summary: "" });
    expect([took(120), took(1400), took(12500)]).toEqual(["120 ms", "1.4 s", "13 s"]);
  });
});
