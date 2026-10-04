import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";

import { ChatTab } from "@/components/recording/chat-tab";
import { RecordingProvider, type RecordingCtx } from "@/components/recording/context";
import { PlayerProvider } from "@/components/player/media";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Chats: {
    listChats: () => Promise.resolve({ data: [], response: { ok: true, status: 200 } }),
    getChat: () => Promise.resolve({ data: null, response: { ok: true, status: 200 } }),
  },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));

beforeAll(() => {
  // Current Chromium returns a Promise from scrollIntoView
  Element.prototype.scrollIntoView = jest.fn(() => Promise.resolve()) as unknown as Element["scrollIntoView"];
});

it("opens with a quote from Ask in chat without React's effect error", async () => {
  const error = jest.spyOn(console, "error").mockImplementation(() => {});
  const ctx = {
    id: 9,
    ns: "pods",
    member: true,
    model: { segments: [] },
    chatDraft: "> “the capsid model” (0:04)\n\n",
    clearChatDraft: () => {},
  } as unknown as RecordingCtx;
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <PlayerProvider hasMedia durationMs={60000}>
          <RecordingProvider value={ctx}>
            <ChatTab />
          </RecordingProvider>
        </PlayerProvider>
      </TooltipProvider>
    </QueryClientProvider>,
  );

  expect(await screen.findByLabelText("Ask about this recording")).toHaveValue("> “the capsid model” (0:04)\n\n");
  expect(error.mock.calls.flat().join(" ")).not.toMatch(/must not return anything besides a function/);
  error.mockRestore();
});
