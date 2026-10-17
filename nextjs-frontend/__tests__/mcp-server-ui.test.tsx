import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";
import type { ReactNode } from "react";

import { Extensions } from "@/app/openapi-client";
import { ExtensionEditor } from "@/components/extensions/extension-editor";
import { McpServerForm } from "@/components/extensions/mcp-server-form";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Extensions: {
    readMcpConfig: jest.fn(),
    testMcpServer: jest.fn(),
    createExtension: jest.fn(),
    getExtension: jest.fn(),
    switchMcpTool: jest.fn(),
    testExtension: jest.fn(),
    updateExtension: jest.fn(),
    deleteExtension: jest.fn(),
  },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
const push = jest.fn();
jest.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
const toast = jest.fn();
jest.mock("@/components/ui/toast", () => ({ useToast: () => toast }));
const archive = { admin: true, namespaces: [{ name: "pods" }], can: () => true };
jest.mock("@/lib/hooks/session", () => ({ useArchive: () => archive }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const m = (f: unknown) => f as jest.Mock;

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>{ui}</TooltipProvider>
    </QueryClientProvider>,
  );
}

const tracker = {
  name: "tracker",
  description: "The tracker MCP server",
  spec: { url: "https://mcp.example.com/mcp", headers: { Authorization: "Bearer ${TRACKER_TOKEN}" } },
  fill: ["TRACKER_TOKEN"],
};
const fetcher = {
  name: "fetch",
  description: "The fetch MCP server",
  spec: { command: "uvx", args: ["mcp-server-fetch"] },
  fill: [],
};

describe("adding an MCP server", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    archive.admin = true;
  });

  it("reads a pasted config, fills in its token, tests it and saves it with Enter", async () => {
    m(Extensions.readMcpConfig).mockReturnValue(ok({ servers: [tracker], skipped: [] }));
    m(Extensions.testMcpServer).mockReturnValue(
      ok({ tools: [{ name: "search", description: "Search issues", read_only: true }, { name: "create" }] }),
    );
    m(Extensions.createExtension).mockReturnValue(ok({ ok: true, id: 4 }));
    wrap(<McpServerForm />);
    fireEvent.click(screen.getByRole("radio", { name: /Paste a config/ }));
    fireEvent.change(screen.getByLabelText("Config"), { target: { value: JSON.stringify({ mcpServers: {} }) } });
    fireEvent.click(screen.getByRole("button", { name: "Read it" }));
    // one server: straight into the form, its token to fill in
    const name = await screen.findByLabelText("Name");
    expect(name).toHaveValue("tracker");
    expect(screen.getByLabelText("Address")).toHaveValue("https://mcp.example.com/mcp");
    expect(screen.getByText(/Fill in TRACKER_TOKEN/)).toBeInTheDocument();
    const token = screen.getByLabelText("Headers: value of Authorization");
    expect(token).toHaveAttribute("type", "password");
    fireEvent.change(token, { target: { value: "Bearer t0ken" } });
    expect(screen.queryByText(/Fill in TRACKER_TOKEN/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    expect(await screen.findByText("search")).toBeInTheDocument();
    expect(screen.getByText("reads")).toBeInTheDocument();
    expect(screen.getByText("asks first")).toBeInTheDocument();
    expect(m(Extensions.testMcpServer).mock.calls[0][0].body).toEqual({
      spec: { url: "https://mcp.example.com/mcp", headers: { Authorization: "Bearer t0ken" } },
    });

    fireEvent.submit(name);
    await waitFor(() => expect(push).toHaveBeenCalledWith("/extensions/4"));
    expect(m(Extensions.createExtension).mock.calls[0][0].body.manifest).toEqual({
      name: "tracker",
      kind: "mcp",
      description: "The tracker MCP server",
      url: "https://mcp.example.com/mcp",
      headers: { Authorization: "Bearer t0ken" },
    });
  });

  it("adds every server of a config at once, and says what it left out", async () => {
    const other = { ...fetcher, name: "time", spec: { command: "uvx", args: ["mcp-server-time"] } };
    m(Extensions.readMcpConfig).mockReturnValue(
      ok({ servers: [fetcher, other], skipped: [{ name: "old", why: "it uses the older SSE transport" }] }),
    );
    m(Extensions.createExtension)
      .mockReturnValueOnce(ok({ ok: true, id: 5 }))
      .mockReturnValueOnce(ok({ ok: true, id: 6 }));
    wrap(<McpServerForm />);
    fireEvent.click(screen.getByRole("radio", { name: /Paste a config/ }));
    fireEvent.change(screen.getByLabelText("Config"), { target: { value: "{}" } });
    fireEvent.click(screen.getByRole("button", { name: "Read it" }));
    expect(await screen.findByText(/it uses the older SSE transport/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add all 2" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/extensions"));
    expect(m(Extensions.createExtension).mock.calls.map((c) => c[0].body.manifest)).toEqual([
      { name: "fetch", kind: "mcp", description: "The fetch MCP server", command: "uvx", args: ["mcp-server-fetch"] },
      { name: "time", kind: "mcp", description: "The fetch MCP server", command: "uvx", args: ["mcp-server-time"] },
    ]);
  });

  it("is for admins", () => {
    archive.admin = false;
    wrap(<McpServerForm />);
    expect(screen.getByText("Only admins add MCP servers")).toBeInTheDocument();
  });

  it("switches a server's tools one by one", async () => {
    m(Extensions.getExtension).mockReturnValue(
      ok({
        id: 3,
        name: "tracker",
        kind: "mcp",
        enabled: true,
        editable: true,
        current: 1,
        version: 1,
        visibility: "everyone",
        namespaces: [],
        manifest: "name: tracker\nkind: mcp\n",
        history: [],
        spec: {
          url: "https://mcp.example.com/mcp",
          headers: { Authorization: "••••" },
          tools: [
            { name: "search", read_only: true },
            { name: "create-issue", read_only: false },
          ],
          off: ["create-issue"],
        },
      }),
    );
    m(Extensions.switchMcpTool).mockReturnValue(ok({ ok: true, version: 2 }));
    wrap(<ExtensionEditor id={3} />);
    const search = await screen.findByRole("switch", { name: "search on" });
    expect(search).toBeChecked();
    expect(screen.getByRole("switch", { name: "create-issue on" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "search asks first" })).not.toBeChecked();
    expect(screen.queryByRole("checkbox", { name: "create-issue asks first" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: "search asks first" }));
    await waitFor(() =>
      expect(Extensions.switchMcpTool).toHaveBeenCalledWith(
        expect.objectContaining({ path: { eid: 3 }, body: { tool: "search", ask: true } }),
      ),
    );
    fireEvent.click(screen.getByRole("switch", { name: "create-issue on" }));
    await waitFor(() =>
      expect(Extensions.switchMcpTool).toHaveBeenLastCalledWith(
        expect.objectContaining({ body: { tool: "create-issue", on: true } }),
      ),
    );
    // tried by the name the assistant calls it, only the tools that are on
    expect(screen.getByRole("button", { name: "Run" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Tool")).not.toBeInTheDocument();
  });
});
