jest.mock("next-auth/react", () => ({ signIn: jest.fn() }));
jest.mock("@/app/openapi-client", () => ({ Auth: { passkeyOptions: jest.fn(), passkeyLogin: jest.fn() } }));
jest.mock("@/lib/auth/webauthn", () => ({
  anonymousClient: () => ({}),
  signWithPasskey: jest.fn(),
  createPasskey: jest.fn(),
}));

import { Auth } from "@/app/openapi-client";
import { passkeyTicket } from "@/components/auth/passkey-flows";
import { signWithPasskey } from "@/lib/auth/webauthn";

const signal = jest.fn().mockResolvedValue(undefined);

beforeEach(() => {
  (window as unknown as { PublicKeyCredential: unknown }).PublicKeyCredential = { signalUnknownCredential: signal };
  (Auth.passkeyOptions as jest.Mock).mockResolvedValue({ data: { flow: "f", options: {} } });
  (signWithPasskey as jest.Mock).mockResolvedValue({ id: "old-key" });
  signal.mockClear();
});

describe("passkeyTicket", () => {
  it("asks the browser to forget a passkey Lens doesn't know", async () => {
    (Auth.passkeyLogin as jest.Mock).mockResolvedValue({
      error: { detail: "this passkey isn't one Lens knows" },
      response: { status: 404 },
    });
    await expect(passkeyTicket()).rejects.toThrow("This passkey isn't one Lens knows.");
    expect(signal).toHaveBeenCalledWith({ rpId: "localhost", credentialId: "old-key" });
  });

  it("leaves the browser's passkeys alone otherwise", async () => {
    (Auth.passkeyLogin as jest.Mock).mockResolvedValue({ data: { ticket: "lt_x" }, response: { status: 200 } });
    await expect(passkeyTicket()).resolves.toBe("lt_x");
    (Auth.passkeyLogin as jest.Mock).mockResolvedValue({ error: { detail: "nope" }, response: { status: 401 } });
    await expect(passkeyTicket()).rejects.toThrow("Nope.");
    expect(signal).not.toHaveBeenCalled();
  });
});
