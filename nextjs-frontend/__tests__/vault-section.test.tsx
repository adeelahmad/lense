import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom";

import type { VaultStatus } from "@/app/openapi-client/types.gen";
import { TooltipProvider } from "@/components/ui/tooltip";
import { VaultSection } from "@/components/vaults/vault-section";

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const fail = (status: number, detail: string) =>
  Promise.resolve({ error: { detail }, response: { ok: false, status } });
type Fn = jest.Mock<Promise<unknown>, [unknown]>;
const api: Record<string, Fn> = {
  getVault: jest.fn(),
  vaultOptions: jest.fn(),
  sealVault: jest.fn(),
  unlockVault: jest.fn(),
  addVaultPasskey: jest.fn(),
  removeVaultPasskey: jest.fn(),
  lockVault: jest.fn(),
  unsealVault: jest.fn(),
};
jest.mock("@/app/openapi-client", () => ({
  Vaults: new Proxy({}, { get: (_t, k: string) => (a: unknown) => api[k](a) }),
}));
const sign = jest.fn();
jest.mock("@/lib/auth/webauthn", () => ({
  ...jest.requireActual("@/lib/auth/webauthn"),
  passkeysUnavailableReason: () => null,
  signWithPasskeyPrf: (o: unknown) => sign(o),
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
const toast = jest.fn();
jest.mock("@/components/ui/toast", () => ({ useToast: () => toast }));

const plain: VaultStatus = { vault: false, unlocked: false, unlocked_until: null, passkeys: [] };
const laptop = { id: "p1", name: "Laptop", account: 1, email: "ada@x.io" };
const open = (passkeys = [laptop]): VaultStatus => ({
  vault: true,
  unlocked: true,
  unlocked_until: Date.now() / 1000 + 3600,
  passkeys,
});

function show(isOwner = true) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <VaultSection ns="pods" isOwner={isOwner} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  api.vaultOptions.mockImplementation(() => ok({ flow: "f1", options: { challenge: "AA" } }));
  sign.mockResolvedValue({ credential: { id: "c" }, prf: "secret" });
});

describe("a namespace's vault", () => {
  it("locks to your passkey after saying what that means", async () => {
    api.getVault.mockImplementation(() => ok(plain));
    api.sealVault.mockImplementation(() => ok(open()));
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Lock to my passkey" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/its files are gone/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Lock it" }));
    await waitFor(() => expect(api.sealVault).toHaveBeenCalled());
    expect(api.vaultOptions.mock.calls[0][0]).toMatchObject({ path: { name: "pods" }, body: { kind: "seal" } });
    expect(api.sealVault.mock.calls[0][0]).toMatchObject({
      body: { flow: "f1", credential: { id: "c" }, prf: "secret" },
    });
    expect(await screen.findByText(/Unlocked until/)).toBeInTheDocument();
    expect(screen.getByText(/losing this one loses its files/)).toBeInTheDocument();
  });

  it("unlocks a locked vault, and members who aren't owners can too", async () => {
    api.getVault.mockImplementation(() => ok({ ...open(), unlocked: false, unlocked_until: null }));
    api.unlockVault.mockImplementation(() => ok(open()));
    show(false);
    expect(await screen.findByText("Locked")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add one of my passkeys" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Unlock with my passkey" }));
    expect(await screen.findByText(/Unlocked until/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Lock now" })).not.toBeInTheDocument(); // owners lock it
    expect(api.vaultOptions.mock.calls[0][0]).toMatchObject({ body: { kind: "unlock" } });
  });

  it("says why a passkey couldn't open it", async () => {
    api.getVault.mockImplementation(() => ok({ ...open(), unlocked: false }));
    api.unlockVault.mockImplementation(() =>
      fail(400, "this passkey can't open vaults (it doesn't support the PRF extension)"),
    );
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Unlock with my passkey" }));
    expect(await screen.findByText(/doesn't support the PRF extension/)).toBeInTheDocument();
  });

  it("never lets the only passkey go, and adds another while it's open", async () => {
    api.getVault.mockImplementation(() => ok(open()));
    const phone = { id: "p2", name: "Phone", account: 1, email: "ada@x.io" };
    api.addVaultPasskey.mockImplementation(() => ok(open([laptop, phone])));
    api.removeVaultPasskey.mockImplementation(() => ok(open([phone])));
    show();
    expect(await screen.findByRole("button", { name: /The only passkey that opens it/ })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Add one of my passkeys" }));
    expect(await screen.findByText("Phone")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "Stop this passkey opening the vault" })[0]);
    await waitFor(() => expect(screen.queryByText("Laptop")).not.toBeInTheDocument());
    expect(api.removeVaultPasskey.mock.calls[0][0]).toMatchObject({ path: { name: "pods", pid: "p1" } });
  });

  it("turns back into an ordinary namespace", async () => {
    api.getVault.mockImplementation(() => ok(open()));
    api.unsealVault.mockImplementation(() => ok(plain));
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Make it ordinary again" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Make it ordinary" }));
    expect(await screen.findByRole("button", { name: "Lock to my passkey" })).toBeInTheDocument();
  });
});
