import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Podcasts, Voice } from "@/app/openapi-client";

import { CreatePodcastDialog } from "../create-dialog";

jest.mock("@/app/openapi-client", () => ({ Podcasts: { createPodcast: jest.fn() }, Voice: { listVoices: jest.fn() } }));
jest.mock("@/lib/api/browser", () => ({
  useApiClient: () => mockClient,
  data: async (call: Promise<unknown>) => call,
}));
jest.mock("@/components/ui/toast", () => ({ useToast: () => mockToast }));
jest.mock("next/navigation", () => ({ useRouter: () => ({ push: mockPush }) }));
const mockClient = {};
const mockToast = jest.fn();
const mockPush = jest.fn();

function show(open = true, recordings = [7]) {
  const onOpenChange = jest.fn();
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const result = render(
    <QueryClientProvider client={queryClient}>
      <CreatePodcastDialog
        open={open}
        onOpenChange={onOpenChange}
        recordings={recordings}
        excerpts={[{ recording: 9, idx: 2 }]}
        what="selected resources"
      />
    </QueryClientProvider>,
  );
  return { ...result, onOpenChange };
}

beforeEach(() => {
  jest.mocked(Voice.listVoices).mockResolvedValue({
    provider: "openai",
    voices: [
      { id: "nova-id", name: "Nova" },
      { id: "alloy-id", name: "Alloy" },
    ],
  } as never);
  jest
    .mocked(Podcasts.createPodcast)
    .mockResolvedValue({ episode: 21, namespace: "podcasts", placed: "podcasts" } as never);
});

it("keeps the original dialogue, length and style defaults, and source selection", async () => {
  show();
  await screen.findAllByRole("option", { name: "Nova" });
  expect(screen.getByLabelText("Output")).toHaveValue("dialogue");
  expect(screen.getByLabelText("Style")).toHaveValue("deep-dive");
  expect(screen.getByLabelText("Length")).toHaveValue("10");
  fireEvent.change(screen.getByLabelText("First host's voice"), { target: { value: "nova-id" } });
  fireEvent.change(screen.getByLabelText("Second host's voice"), { target: { value: "alloy-id" } });
  fireEvent.click(screen.getByRole("button", { name: "Create podcast" }));
  await waitFor(() => expect(mockPush).toHaveBeenCalledWith("/podcasts/21"));
  expect(Podcasts.createPodcast).toHaveBeenCalledWith({
    client: mockClient,
    body: {
      selection: { recordings: [7], excerpts: [{ recording: 9, idx: 2 }] },
      mode: "dialogue",
      voices: { a: "nova-id", b: "alloy-id" },
      prompt: null,
      length: 10,
      style: "deep-dive",
      title: null,
    },
  });
});

it("hides rewriting controls and drops the second voice and angle for original narration", async () => {
  show();
  await screen.findAllByRole("option", { name: "Nova" });
  fireEvent.change(screen.getByLabelText("Second host's voice"), { target: { value: "alloy-id" } });
  fireEvent.change(screen.getByLabelText(/Angle/), { target: { value: "Explain everything" } });
  fireEvent.change(screen.getByLabelText("Output"), { target: { value: "original" } });
  expect(screen.queryByLabelText("Second host's voice")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Length")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Style")).not.toBeInTheDocument();
  expect(screen.queryByLabelText(/Angle/)).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Narrator's voice"), { target: { value: "nova-id" } });
  fireEvent.click(screen.getByRole("button", { name: "Create podcast" }));
  await waitFor(() =>
    expect(Podcasts.createPodcast).toHaveBeenCalledWith(
      expect.objectContaining({
        body: expect.objectContaining({ mode: "original", voices: { a: "nova-id", b: null }, prompt: null }),
      }),
    ),
  );
});

it.each(["summary", "explanation"])("uses one narrator for %s", async (mode) => {
  show();
  await screen.findAllByRole("option", { name: "Nova" });
  fireEvent.change(screen.getByLabelText("Output"), { target: { value: mode } });
  expect(screen.getByLabelText("Narrator's voice")).toBeInTheDocument();
  expect(screen.queryByLabelText("Second host's voice")).not.toBeInTheDocument();
  expect(screen.getByLabelText("Length")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Create podcast" }));
  await waitFor(() =>
    expect(Podcasts.createPodcast).toHaveBeenCalledWith(
      expect.objectContaining({ body: expect.objectContaining({ mode, voices: null }) }),
    ),
  );
});

it("allows saved defaults when voice discovery fails", async () => {
  jest.mocked(Voice.listVoices).mockRejectedValue(new Error("Provider offline"));
  show();
  await screen.findByRole("button", { name: "Retry voice list" });
  fireEvent.click(screen.getByRole("button", { name: "Create podcast" }));
  await waitFor(() =>
    expect(Podcasts.createPodcast).toHaveBeenCalledWith(
      expect.objectContaining({ body: expect.objectContaining({ voices: null }) }),
    ),
  );
});

it("does not fetch voices while the dialog is closed", () => {
  show(false);
  expect(Voice.listVoices).not.toHaveBeenCalled();
});

it("prevents duplicate creation while the first request is pending", async () => {
  jest.mocked(Podcasts.createPodcast).mockImplementation(() => new Promise<never>(() => undefined));
  const { onOpenChange } = show();
  await screen.findAllByRole("option", { name: "Nova" });
  const create = screen.getByRole("button", { name: "Create podcast" });
  fireEvent.click(create);
  fireEvent.click(create);
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(Podcasts.createPodcast).toHaveBeenCalledTimes(1);
  expect(onOpenChange).not.toHaveBeenCalled();
});
