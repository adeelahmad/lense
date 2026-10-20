import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Chats } from "@/app/openapi-client";
import { Answer } from "@/components/chat/answer";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Voice: { voiceInfo: jest.fn(() => new Promise(() => {})), speakText: jest.fn() },
  Chats: { checkMessage: jest.fn(), rateMessage: jest.fn() },
  Search: {},
}));
jest.mock("@/lib/api/browser", () => ({
  useApiClient: () => ({}),
  data: (p: Promise<{ data: unknown }>) => p.then((r) => r.data),
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("@/lib/hooks/session", () => ({
  useArchive: () => ({ can: () => true, namespaces: [], roleIn: () => "editor" }),
}));

const rate = Chats.rateMessage as jest.Mock;

beforeAll(() => {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  })) as unknown as typeof window.matchMedia;
});
beforeEach(() => jest.clearAllMocks());

function show(rating: "up" | "down" | null = null, status: "done" | "streaming" = "done") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <Answer
          chatId={1}
          messageId={7}
          question="When does it leave?"
          text="On Friday."
          passages={[]}
          status={status}
          scope={{}}
          model="m"
          hover={null}
          onHover={() => {}}
          onPreview={() => {}}
          rating={rating}
        />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

test("thumbs up saves the rating, and pressing it again takes it back", async () => {
  rate.mockImplementation(({ body }) => Promise.resolve({ data: { id: 7, rating: body.rating } }));
  show();
  const up = screen.getByRole("button", { name: "Good answer" });
  expect(up).toHaveAttribute("aria-pressed", "false");
  fireEvent.click(up);
  await waitFor(() =>
    expect(rate).toHaveBeenCalledWith(expect.objectContaining({ path: { cid: 1, mid: 7 }, body: { rating: "up" } })),
  );
  const given = await screen.findByRole("button", { name: "Good answer (press to take back)" });
  expect(given).toHaveAttribute("aria-pressed", "true");
  fireEvent.click(given);
  await waitFor(() => expect(rate).toHaveBeenLastCalledWith(expect.objectContaining({ body: { rating: null } })));
  await screen.findByRole("button", { name: "Good answer" });
});

test("a saved thumbs down shows pressed, and a failed save puts it back", async () => {
  rate.mockRejectedValue(new Error("offline"));
  show("down");
  expect(screen.getByRole("button", { name: "Bad answer (press to take back)" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  fireEvent.click(screen.getByRole("button", { name: "Good answer" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Couldn’t save the rating: offline");
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Bad answer (press to take back)" })).toHaveAttribute(
      "aria-pressed",
      "true",
    ),
  );
});

test("no thumbs while the answer is still being written", () => {
  show(null, "streaming");
  expect(screen.queryByRole("group", { name: "Rate this answer" })).not.toBeInTheDocument();
});
