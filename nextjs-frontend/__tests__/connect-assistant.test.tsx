import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { ConnectedApps } from "@/components/account/apps";
import { connectHint } from "@/components/account/connect-assistant";
import { AccountMenu } from "@/components/app-shell/account-menu";
import { TooltipProvider } from "@/components/ui/tooltip";

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
jest.mock("@/app/openapi-client", () => ({
  Oauth: { listGrants: () => ok([]), revokeGrant: () => ok({}) },
  Tokens: { listTokens: () => ok([]) },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }), signOut: jest.fn() }));
jest.mock("@/lib/hooks/session", () => ({
  useArchive: () => ({ me: { user: { name: "Vi", email: "vi@x.io" }, roles: {} }, admin: false }),
}));
const toast = jest.fn();
jest.mock("@/components/ui/toast", () => ({ useToast: () => toast }));

const MCP = `${window.location.origin}/mcp`;
let writeText: jest.Mock;
let signIn: boolean;

beforeEach(() => {
  signIn = true;
  writeText = jest.fn(() => Promise.resolve());
  Object.assign(navigator, { clipboard: { writeText } });
  // OAuth discovery answers only while apps may sign in
  global.fetch = jest.fn(() => Promise.resolve({ ok: signIn } as Response));
});

function show(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>{ui}</TooltipProvider>
    </QueryClientProvider>,
  );
}

describe("connecting an assistant", () => {
  it("shows the MCP address with Copy above the apps with access", async () => {
    show(<ConnectedApps />);
    expect(await screen.findByText(MCP)).toBeInTheDocument();
    expect(await screen.findByText(/Add it to Claude, ChatGPT, Cursor/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Copy MCP address" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(MCP));
    expect(await screen.findByRole("button", { name: "Copied" })).toBeInTheDocument();
  });

  it("says to give an API token while apps can't sign in", async () => {
    signIn = false;
    show(<ConnectedApps />);
    expect(await screen.findByText(/give the assistant an API token/)).toBeInTheDocument();
    expect(connectHint(undefined)).toMatch(/as a connector/);
  });

  it("copies the address from the account menu in one click", async () => {
    show(<AccountMenu name="Vi" email="vi@x.io" />);
    fireEvent.keyDown(screen.getByRole("button", { name: "Account menu" }), { key: "Enter" });
    fireEvent.click(await screen.findByRole("menuitem", { name: "Copy MCP address" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(MCP));
    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(
        expect.objectContaining({ title: "MCP address copied", body: expect.stringContaining(MCP), tone: "green" }),
      ),
    );
  });

  it("shows the address when the browser won't copy", async () => {
    writeText.mockRejectedValue(new Error("denied"));
    show(<AccountMenu name="Vi" email="vi@x.io" />);
    fireEvent.keyDown(screen.getByRole("button", { name: "Account menu" }), { key: "Enter" });
    fireEvent.click(await screen.findByRole("menuitem", { name: "Copy MCP address" }));
    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(
        expect.objectContaining({ title: "Copy the MCP address", body: expect.stringContaining(MCP) }),
      ),
    );
  });
});
