import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Auth } from "@/app/openapi-client";
import { ExternalSignIn } from "@/components/auth/external-sign-in";
import { ExternalSigninReturn, safeNext } from "@/components/auth/external-signin-return";
import { finishSignIn } from "@/components/auth/passkey-flows";
import { LoginForm } from "@/components/auth/login-form";

jest.mock("@/components/actions/login-action", () => ({ login: jest.fn() }));
jest.mock("@/components/auth/passkey-flows", () => ({ passkeyTicket: jest.fn(), finishSignIn: jest.fn() }));
jest.mock("@/app/openapi-client", () => ({ Auth: { externalStart: jest.fn() } }));
jest.mock("@/lib/auth/webauthn", () => ({
  ...jest.requireActual("@/lib/auth/webauthn"),
  anonymousClient: () => ({}),
}));

const GOOGLE = { key: "google", kind: "google" as const, label: "Google" };
const GITHUB = { key: "github", kind: "github" as const, label: "GitHub" };

const assign = jest.fn();
const replace = jest.fn();
beforeAll(() => {
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...window.location, assign, replace, hash: "", pathname: "/external-signin", hostname: "localhost" },
  });
});
afterEach(() => jest.clearAllMocks());

function at(hash: string) {
  (window.location as { hash: string }).hash = hash;
}

describe("signing in with an outside account", () => {
  it("offers each provider on the sign-in page and opens the one picked", async () => {
    (Auth.externalStart as jest.Mock).mockResolvedValue({ data: { url: "https://accounts.google.com/x" } });
    render(<LoginForm providers={[GOOGLE, GITHUB]} callbackUrl="/recordings" />);

    expect(screen.getByRole("button", { name: "Continue with GitHub" })).toBeInTheDocument();
    // passkeys can't work here (jsdom isn't a secure context) and there's another way in: no passkey alert
    expect(screen.queryByText(/passkeys only work on https/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Continue with Google" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://accounts.google.com/x"));
    expect(Auth.externalStart).toHaveBeenCalledWith(
      expect.objectContaining({ path: { key: "google" }, body: { next: "/recordings" } }),
    );
  });

  it("says why it couldn't start", async () => {
    (Auth.externalStart as jest.Mock).mockResolvedValue({ error: { detail: "signing in with Google is turned off" } });
    render(<ExternalSignIn providers={[GOOGLE]} />);
    fireEvent.click(screen.getByRole("button", { name: "Continue with Google" }));
    expect(await screen.findByText(/turned off/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Continue with Google" })).toBeEnabled();
  });

  it("shows nothing without providers", () => {
    const { container } = render(<ExternalSignIn providers={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("coming back from the provider", () => {
  it("swaps the ticket for a session and goes on", async () => {
    (finishSignIn as jest.Mock).mockResolvedValue(undefined);
    at("#ticket=lt_abc&next=%2Frecordings");
    render(<ExternalSigninReturn />);
    await waitFor(() => expect(finishSignIn).toHaveBeenCalledWith("lt_abc", "/recordings"));
  });

  it("says why it didn't work, with the way back", async () => {
    at("#error=no+Lens+account+uses+eve%40x.io&next=%2Flogin");
    render(<ExternalSigninReturn />);
    expect(await screen.findByText("No Lens account uses eve@x.io.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to sign in" })).toHaveAttribute("href", "/login");
  });

  it("goes back to the profile after connecting one", async () => {
    at("#connected=Google&next=%2Faccount");
    render(<ExternalSigninReturn />);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/account?connected=Google"));
  });

  it("only ever goes to a page of this site", () => {
    expect(safeNext("//evil.example")).toBe("/");
    expect(safeNext("https://evil.example")).toBe("/");
    expect(safeNext("/\\evil.example")).toBe("/");
    expect(safeNext("/recordings?x=1")).toBe("/recordings?x=1");
  });
});
