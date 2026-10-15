import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Namespaces } from "@/app/openapi-client";
import { ConnectAppsSection } from "@/components/integrations/connect-apps-section";
import { namespaceOf } from "@/components/oauth/model";

jest.mock("@/app/openapi-client", () => ({
  Namespaces: { getNamespaceIntegrations: jest.fn(), updateNamespaceIntegrations: jest.fn() },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const m = (f: unknown) => f as jest.Mock;
const OFF = { mcp: false, mcp_url: "http://localhost:3000/mcp/pods" };

function show(isOwner = true) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ConnectAppsSection ns="pods" isOwner={isOwner} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  global.fetch = jest.fn().mockResolvedValue({ ok: true }) as unknown as typeof fetch;
  m(Namespaces.getNamespaceIntegrations).mockReturnValue(ok(OFF));
});

test("owners turn the namespace's MCP server on, and its address is there to copy", async () => {
  m(Namespaces.updateNamespaceIntegrations).mockReturnValue(ok({ ...OFF, mcp: true }));
  show();
  const sw = await screen.findByRole("switch", { name: "pods’s MCP server" });
  expect(screen.queryByText(`${window.location.origin}/mcp/pods`)).not.toBeInTheDocument(); // off: nothing to copy
  fireEvent.click(sw);
  await waitFor(() =>
    expect(Namespaces.updateNamespaceIntegrations).toHaveBeenCalledWith(
      expect.objectContaining({ path: { name: "pods" }, body: { mcp: true } }),
    ),
  );
  expect(await screen.findByText(`${window.location.origin}/mcp/pods`)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Copy MCP address" })).toBeInTheDocument();
});

test("people who don't own the namespace see whether it's on, and who can change it", async () => {
  m(Namespaces.getNamespaceIntegrations).mockReturnValue(ok({ ...OFF, mcp: true }));
  show(false);
  expect(await screen.findByRole("switch", { name: "pods’s MCP server" })).toBeDisabled();
  expect(screen.getByText(`${window.location.origin}/mcp/pods`)).toBeInTheDocument();
});

test("an app signing in for one namespace's server is asking for that namespace alone", () => {
  expect(namespaceOf("https://lens.example.org/mcp/pods")).toBe("pods");
  expect(namespaceOf("https://lens.example.org/mcp/pods/")).toBe("pods");
  expect(namespaceOf("https://lens.example.org/mcp")).toBeNull();
  expect(namespaceOf("https://lens.example.org/")).toBeNull();
  expect(namespaceOf("not a url")).toBeNull();
  expect(namespaceOf(undefined)).toBeNull();
});
