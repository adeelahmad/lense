import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Namespaces } from "@/app/openapi-client";
import { NsAssistantSection } from "@/components/assistant/ns-assistant-section";

jest.mock("@/app/openapi-client", () => ({
  Namespaces: {
    getNamespaceAssistant: jest.fn(),
    listAssistantMemories: jest.fn(),
    updateNamespaceAssistant: jest.fn(),
    addAssistantMemory: jest.fn(),
    updateAssistantMemory: jest.fn(),
    forgetAssistantMemory: jest.fn(),
  },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const m = (f: unknown) => f as jest.Mock;
const OFF = { enabled: false, name: "Assistant", instructions: "", memories: 2 };
const MEMORIES = [
  { id: 2, text: "Episodes ship on Fridays.", author: "person", pinned: true, created_at: "2026-10-05T01:00:00Z" },
  {
    id: 1,
    text: "Dyno designs capsids.",
    author: "assistant",
    pinned: false,
    recording: 7,
    t0: 65000,
    title: "Episode 1",
    time: "1:05",
    created_at: "2026-10-04T01:00:00Z",
  },
];

function show(isOwner = true) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <NsAssistantSection ns="pods" isOwner={isOwner} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  m(Namespaces.getNamespaceAssistant).mockReturnValue(ok(OFF));
  m(Namespaces.listAssistantMemories).mockReturnValue(ok(MEMORIES));
});

test("lists memories with where they came from, and owners turn the assistant on", async () => {
  m(Namespaces.updateNamespaceAssistant).mockReturnValue(ok({ ...OFF, enabled: true }));
  show();
  expect(await screen.findByText("Dyno designs capsids.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Episode 1 at 1:05" })).toHaveAttribute("href", "/resources/7?t=65");
  expect(screen.getByText("Written here", { exact: false })).toBeInTheDocument();
  expect(screen.getByText("Pinned")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("switch", { name: "pods’s assistant" }));
  await waitFor(() =>
    expect(Namespaces.updateNamespaceAssistant).toHaveBeenCalledWith(
      expect.objectContaining({ body: { enabled: true } }),
    ),
  );
});

test("saving a name and forgetting a memory", async () => {
  m(Namespaces.updateNamespaceAssistant).mockReturnValue(ok({ ...OFF, name: "Pod pal" }));
  m(Namespaces.forgetAssistantMemory).mockReturnValue(ok({ ok: true }));
  show();
  const name = await screen.findByLabelText("Name");
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  fireEvent.change(name, { target: { value: "Pod pal" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(Namespaces.updateNamespaceAssistant).toHaveBeenCalledWith(
      expect.objectContaining({ body: { name: "Pod pal", instructions: "" } }),
    ),
  );
  fireEvent.click(screen.getByRole("button", { name: "Forget: Dyno designs capsids." }));
  await waitFor(() =>
    expect(Namespaces.forgetAssistantMemory).toHaveBeenCalledWith(
      expect.objectContaining({ path: { name: "pods", mid: 1 } }),
    ),
  );
});

test("only owners change it", async () => {
  show(false);
  expect(await screen.findByLabelText("Name")).toBeDisabled();
  expect(screen.getByRole("switch", { name: "pods’s assistant" })).toBeDisabled();
  expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
});
