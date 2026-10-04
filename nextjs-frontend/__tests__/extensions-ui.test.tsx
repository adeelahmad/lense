import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";
import type { ReactNode } from "react";

import { Extensions } from "@/app/openapi-client";
import { ExtensionEditor } from "@/components/extensions/extension-editor";
import { ExtensionsPage } from "@/components/extensions/extensions-page";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Extensions: {
    listExtensions: jest.fn(),
    createExtension: jest.fn(),
    checkManifest: jest.fn(),
    getExtension: jest.fn(),
    updateExtension: jest.fn(),
    createExtensionVersion: jest.fn(),
    deleteExtension: jest.fn(),
    testExtension: jest.fn(),
  },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
const push = jest.fn();
jest.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
const toast = jest.fn();
jest.mock("@/components/ui/toast", () => ({ useToast: () => toast }));
jest.mock("@/lib/hooks/session", () => ({ useArchive: () => ({ namespaces: [{ name: "pods" }], can: () => true }) }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const fail = (detail: string) =>
  Promise.resolve({ error: { detail }, response: { ok: false, status: 400, statusText: "Bad Request" } });
const m = (f: unknown) => f as jest.Mock;

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>{ui}</TooltipProvider>
    </QueryClientProvider>,
  );
}

const tool = {
  id: 7,
  name: "translate",
  kind: "tool",
  description: "Translate text",
  visibility: "private",
  namespaces: [],
  enabled: true,
  editable: true,
  current: 2,
  version: 2,
  origin: "chat",
  spec: {
    effect: "read",
    params: [{ name: "text", kind: "text" }],
    run: { type: "prompt", prompt: "Translate {{text}}" },
  },
  updated_at: null,
};

describe("extensions", () => {
  beforeEach(() => jest.clearAllMocks());

  it("lists extensions and switches one off", async () => {
    m(Extensions.listExtensions).mockReturnValue(ok([tool]));
    m(Extensions.updateExtension).mockReturnValue(ok({ ok: true }));
    wrap(<ExtensionsPage />);
    expect(await screen.findByText("translate")).toBeInTheDocument();
    expect(screen.getByText("prompt")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("switch", { name: "translate on" }));
    await waitFor(() =>
      expect(Extensions.updateExtension).toHaveBeenCalledWith(
        expect.objectContaining({ path: { eid: 7 }, body: { enabled: false } }),
      ),
    );
  });

  it("offers asking the assistant when there are none", async () => {
    m(Extensions.listExtensions).mockReturnValue(ok([]));
    wrap(<ExtensionsPage />);
    expect(await screen.findByText("Nothing added to the assistant yet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Ask the assistant to make one" })).toHaveAttribute(
      "href",
      expect.stringContaining("/chat?q="),
    );
  });

  it("writes a new one from a starter, says what's wrong, and saves it", async () => {
    m(Extensions.checkManifest).mockReturnValue(fail("the tool uses {{words}}, which aren't its parameters"));
    m(Extensions.createExtension).mockReturnValue(ok({ ok: true, id: 9 }));
    wrap(<ExtensionEditor />);
    fireEvent.click(screen.getByRole("radio", { name: "Tool" }));
    expect((screen.getByLabelText("Manifest") as HTMLTextAreaElement).value).toContain("kind: tool");
    fireEvent.click(screen.getByRole("button", { name: "Check" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("{{words}}");
    fireEvent.click(screen.getByRole("button", { name: "Add to the assistant" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/extensions/9"));
    expect(m(Extensions.createExtension).mock.calls[0][0].body.text).toContain("name: translate");
  });

  it("tries a tool", async () => {
    m(Extensions.getExtension).mockReturnValue(ok({ ...tool, manifest: "name: translate\n", history: [] }));
    m(Extensions.testExtension).mockReturnValue(ok({ output: { text: "Bonjour" } }));
    wrap(<ExtensionEditor id={7} />);
    fireEvent.change(await screen.findByLabelText("Arguments (JSON)"), { target: { value: '{"text": "hello"}' } });
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    expect(await screen.findByText(/Bonjour/)).toBeInTheDocument();
    expect(m(Extensions.testExtension).mock.calls[0][0].body).toEqual({
      tool: "translate",
      args: { text: "hello" },
      confirm: false,
    });
  });
});
