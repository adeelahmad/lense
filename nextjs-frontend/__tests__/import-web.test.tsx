import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Imports } from "@/app/openapi-client";
import { webAddress, webAddressProblem } from "@/components/import/files";
import { WebTab } from "@/components/import/web-tab";
import { webRows } from "@/components/recording/document/model";
import { sourceLabel, webHost } from "@/components/recording/labels";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Imports: { importWebPage: jest.fn(), importWebLinks: jest.fn(), importVideo: jest.fn() },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
const toast = jest.fn();
jest.mock("@/components/ui/toast", () => ({ useToast: () => toast }));
const m = (f: unknown) => f as jest.Mock;

function show(blockReason: string | null = null) {
  const qc = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <WebTab
          namespace="pods"
          namespaceControl={<p>Namespace control</p>}
          pipelineControl={<p>Pipeline control</p>}
          blockReason={blockReason}
          pipeline={3}
          collection={null}
        />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

beforeAll(() => {
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});
beforeEach(() => jest.clearAllMocks());

describe("capturing a web page", () => {
  it("checks an address as it's typed; the server checks the rest", () => {
    expect(webAddressProblem("")).toBe("Give the page’s address.");
    expect(webAddressProblem("example.org/news")).toBeNull(); // https:// is added
    expect(webAddress(" localhost:8080/a ")).toBe("https://localhost:8080/a");
    expect(webAddress("http://example.org")).toBe("http://example.org");
    expect(webAddressProblem("mailto:me@example.org")).toBe("Only web pages can be captured: https:// or http://.");
    expect(webAddressProblem("ftp://example.org/a")).toBe("Only web pages can be captured: https:// or http://.");
    expect(webAddressProblem("https://me:pw@example.org/")).toBe(
      "Leave the user name and password out of the address.",
    );
    expect(webAddressProblem(" https://example.org/news ")).toBeNull();
  });

  it("captures the page and lists it, or says why the server wouldn't", async () => {
    m(Imports.importWebPage)
      .mockResolvedValueOnce({ data: { ok: true, id: 41, job: 9 }, response: { ok: true, status: 200 } })
      .mockResolvedValueOnce({
        data: undefined,
        error: { detail: "127.0.0.1 isn't a public address: only public web pages can be captured" },
        response: { ok: false, status: 400 },
      });
    show();
    const address = screen.getByLabelText("Page address");
    fireEvent.click(screen.getByRole("button", { name: "Capture the page" }));
    expect(await screen.findByText("Give the page’s address.")).toBeInTheDocument();
    expect(Imports.importWebPage).not.toHaveBeenCalled();

    fireEvent.change(address, { target: { value: "https://example.org/news/harbour" } });
    fireEvent.change(screen.getByLabelText(/Title/), { target: { value: "Harbour news" } });
    fireEvent.click(screen.getByRole("button", { name: "Capture the page" }));
    await waitFor(() => expect(screen.getByRole("link", { name: "Open" })).toHaveAttribute("href", "/resources/41"));
    expect(m(Imports.importWebPage).mock.calls[0][0].body).toEqual({
      url: "https://example.org/news/harbour",
      namespace: "pods",
      title: "Harbour news",
      pipeline: 3,
      collection: null,
    });
    expect(toast).toHaveBeenCalledWith(expect.objectContaining({ title: "Capturing the page" }));
    expect(address).toHaveValue("");

    fireEvent.change(address, { target: { value: "http://127.0.0.1/admin" } });
    fireEvent.click(screen.getByRole("button", { name: "Capture the page" }));
    expect(await screen.findByText(/only public web pages can be captured/)).toBeInTheDocument();
  });

  it("captures many pages from pasted links, saying what became of each", async () => {
    m(Imports.importWebLinks).mockResolvedValueOnce({
      data: {
        results: [
          { url: "https://example.org/a", status: "queued", recording: 5, job: 7 },
          { url: "https://example.org/b", status: "already", recording: 2 },
          { url: "http://127.0.0.1/x", status: "skipped", detail: "isn't a public address" },
        ],
      },
      response: { ok: true, status: 200 },
    });
    show();
    expect(screen.getByRole("button", { name: "Capture them all" })).toHaveAttribute("aria-disabled", "true");
    fireEvent.change(screen.getByLabelText(/^Links/), {
      target: { value: "https://example.org/a\nhttps://example.org/b" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Capture them all" }));
    await waitFor(() => expect(Imports.importWebLinks).toHaveBeenCalled());
    expect(await screen.findByText("Already here")).toBeInTheDocument();
    expect(screen.getByText("Skipped")).toBeInTheDocument();
    expect(m(Imports.importWebLinks).mock.calls[0][0].body).toEqual({
      namespace: "pods",
      text: "https://example.org/a\nhttps://example.org/b",
      folders_as_tags: true,
      pipeline: 3,
      collection: null,
    });
    expect(toast).toHaveBeenCalledWith(expect.objectContaining({ title: "Capturing 1 page" }));
  });

  it("imports a video or podcast by its address, when asked to", async () => {
    m(Imports.importVideo).mockResolvedValueOnce({
      data: { ok: true, id: 52, job: 10 },
      response: { ok: true, status: 200 },
    });
    show();
    fireEvent.click(screen.getByRole("checkbox", { name: /video or a podcast/ }));
    fireEvent.change(screen.getByLabelText("Page address"), { target: { value: "example.org/watch?v=1" } });
    fireEvent.click(screen.getByRole("button", { name: "Import the video" }));
    await waitFor(() => expect(screen.getByRole("link", { name: "Open" })).toHaveAttribute("href", "/resources/52"));
    expect(Imports.importWebPage).not.toHaveBeenCalled();
    expect(m(Imports.importVideo).mock.calls[0][0].body).toEqual({
      url: "https://example.org/watch?v=1",
      namespace: "pods",
      title: null,
      pipeline: 3,
      collection: null,
    });
    expect(toast).toHaveBeenCalledWith(expect.objectContaining({ title: "Importing the video" }));
  });

  it("waits for a namespace it may import into", () => {
    show("Choose a namespace");
    expect(screen.getByRole("button", { name: "Capture the page" })).toHaveAttribute("aria-disabled", "true");
  });

  it("says where a captured page came from, when and how it was kept", () => {
    const web = {
      url: "https://example.org/a",
      final: "https://www.example.org/a",
      captured_at: "2026-10-01T10:00:00Z",
      how: "printed",
    };
    expect(webRows(web, (iso) => iso.slice(0, 10))).toEqual([
      ["Address", "https://example.org/a"],
      ["Ended at", "https://www.example.org/a"],
      ["Captured", "2026-10-01"],
      ["Kept as", "printed by the server’s browser"],
    ]);
    expect(webRows({ url: "https://x.org/r.pdf" }, String)).toEqual([
      ["Address", "https://x.org/r.pdf"],
      ["Captured", "when its pipeline runs"],
    ]);
    expect(sourceLabel({ web, path: "/data/web/4.pdf" })).toMatchObject({
      text: "Captured from www.example.org",
      href: "https://www.example.org/a",
    });
    expect(webHost("not a url")).toBe("not a url");
  });
});
