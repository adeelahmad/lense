import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Auth } from "@/app/openapi-client";
import { PasskeysPanel } from "@/components/account/passkeys";
import { TooltipProvider } from "@/components/ui/tooltip";
import { navigate } from "@/lib/navigate";

jest.mock("@/app/openapi-client", () => ({
  Auth: {
    listPasskeys: jest.fn(),
    listIdentities: jest.fn(),
    passkeyAddresses: jest.fn(),
    passkeyElsewhere: jest.fn(),
  },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("@/components/ui/toast", () => ({ useToast: () => jest.fn() }));
jest.mock("@/lib/navigate", () => ({ navigate: { assign: jest.fn(), replace: jest.fn() } }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const LAPTOP = {
  id: "p1",
  name: "Laptop",
  rp_id: "localhost",
  backed_up: false,
  created_at: "2026-10-15T10:00:00Z",
  last_used_at: null,
};

function show() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <PasskeysPanel />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

describe("Passkeys at Lens's other addresses", () => {
  const assign = navigate.assign as jest.Mock;

  beforeEach(() => {
    assign.mockReset();
    (Auth.listPasskeys as jest.Mock).mockReset().mockImplementation(() => ok([LAPTOP]));
    (Auth.listIdentities as jest.Mock).mockReset().mockImplementation(() => ok([]));
    (Auth.passkeyElsewhere as jest.Mock).mockReset();
  });

  it("opens an address you have no passkey for signed in, to add one there", async () => {
    (Auth.passkeyAddresses as jest.Mock)
      .mockReset()
      .mockImplementation(() => ok([{ url: "https://lens.example.com", passkey: false }]));
    (Auth.passkeyElsewhere as jest.Mock).mockImplementation(() =>
      ok({ url: "https://lens.example.com/signin-link#abc", expires_at: "2026-10-15T12:15:00Z", emailed: false }),
    );
    show();
    expect(await screen.findByText("https://lens.example.com")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Open signed in" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://lens.example.com/signin-link#abc"));
    expect((Auth.passkeyElsewhere as jest.Mock).mock.calls[0][0].body).toEqual({ url: "https://lens.example.com" });
  });

  it("just links to an address you have a passkey for", async () => {
    (Auth.passkeyAddresses as jest.Mock)
      .mockReset()
      .mockImplementation(() => ok([{ url: "https://lens.example.com", passkey: true }]));
    show();
    const open = await screen.findByRole("link", { name: "Open" });
    expect(open).toHaveAttribute("href", "https://lens.example.com");
    expect(screen.queryByRole("button", { name: "Open signed in" })).not.toBeInTheDocument();
  });

  it("says nothing when Lens opens nowhere else", async () => {
    (Auth.passkeyAddresses as jest.Mock).mockReset().mockImplementation(() => ok([]));
    show();
    expect(await screen.findByText("Laptop")).toBeInTheDocument();
    expect(screen.queryByText(/Lens also opens at/)).not.toBeInTheDocument();
  });
});
