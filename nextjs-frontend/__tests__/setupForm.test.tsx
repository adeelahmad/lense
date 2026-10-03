import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { setup } from "@/components/actions/setup-action";
import { SetupForm } from "@/components/auth/setup-form";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/components/actions/setup-action", () => ({ setup: jest.fn() }));
jest.mock("@/components/auth/passkey-flows", () => {
  class PasskeyFlowError extends Error {
    constructor(
      message: string,
      public status?: number,
    ) {
      super(message);
    }
  }
  return { setupTicket: jest.fn(), finishSignIn: jest.fn(), PasskeyFlowError };
});

function renderForm() {
  return render(
    <TooltipProvider>
      <SetupForm />
    </TooltipProvider>,
  );
}

function fill(values: { code?: string; name?: string; email?: string; password?: string }) {
  for (const [label, value] of [
    ["Setup code", values.code],
    ["Name", values.name],
    ["Email", values.email],
    ["Password", values.password],
  ] as const) {
    if (value !== undefined) fireEvent.change(screen.getByLabelText(label), { target: { value } });
  }
}

const valid = {
  code: "c0de",
  name: "Ada",
  email: "ada@example.com",
  password: "long enough pw",
};

describe("SetupForm", () => {
  it("submits the first admin's details", async () => {
    (setup as jest.Mock).mockResolvedValue(undefined);
    renderForm();

    fill(valid);
    fireEvent.click(screen.getByRole("button", { name: /create admin account/i }));

    await waitFor(() => {
      const expected = new FormData();
      expected.set("code", "c0de");
      expected.set("name", "Ada");
      expected.set("email", "ada@example.com");
      expected.set("password", "long enough pw");
      expect(setup).toHaveBeenCalledWith(undefined, expected);
    });
  });

  it("checks the password as you type and waits for a complete form", () => {
    renderForm();

    fill({ ...valid, password: "lens-arch" });

    expect(screen.getByText("9 of 10 characters — add at least 1 more")).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toHaveAttribute("aria-invalid", "true");
    const button = screen.getByRole("button", {
      name: /create admin account/i,
    });
    expect(button).toHaveAttribute("aria-disabled", "true");

    fireEvent.click(button);
    expect(setup).not.toHaveBeenCalled();
  });

  it("shows a wrong code under the setup code", async () => {
    (setup as jest.Mock).mockResolvedValue({
      errors: {
        code: ["Setup is closed or the code is wrong. Copy the code again from the server log."],
      },
    });
    renderForm();

    fill(valid);
    fireEvent.click(screen.getByRole("button", { name: /create admin account/i }));

    await waitFor(() => expect(screen.getByLabelText("Setup code")).toHaveAttribute("aria-invalid", "true"));
    expect(screen.getByLabelText("Setup code")).toHaveAccessibleDescription(/setup is closed or the code is wrong/i);

    // Editing the code clears its error.
    fill({ code: "c0de2" });
    expect(screen.getByLabelText("Setup code")).not.toHaveAttribute("aria-invalid");
  });

  it("shows other failures above the button", async () => {
    (setup as jest.Mock).mockResolvedValue({
      server_error: "An unexpected error occurred. Please try again later.",
    });
    renderForm();

    fill(valid);
    fireEvent.click(screen.getByRole("button", { name: /create admin account/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent("An unexpected error occurred");
  });
});

describe("SetupForm with passkeys", () => {
  const secure = Object.getOwnPropertyDescriptor(window, "isSecureContext");
  beforeEach(() => {
    Object.defineProperty(window, "isSecureContext", { configurable: true, value: true });
    (window as unknown as { PublicKeyCredential: unknown }).PublicKeyCredential = function PublicKeyCredential() {};
  });
  afterEach(() => {
    if (secure) Object.defineProperty(window, "isSecureContext", secure);
    else delete (window as unknown as { isSecureContext?: boolean }).isSecureContext;
    delete (window as unknown as { PublicKeyCredential?: unknown }).PublicKeyCredential;
    jest.clearAllMocks();
  });

  it("makes the first admin with a passkey and no password", async () => {
    const flows = jest.requireMock("@/components/auth/passkey-flows");
    flows.setupTicket.mockResolvedValue("lt_ticket");
    renderForm();

    expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
    fill({ code: "c0de", name: "Ada", email: "ada@example.com" });
    fireEvent.click(screen.getByRole("button", { name: /create admin with a passkey/i }));

    await waitFor(() => expect(flows.finishSignIn).toHaveBeenCalledWith("lt_ticket", "/welcome"));
    expect(flows.setupTicket).toHaveBeenCalledWith({ code: "c0de", email: "ada@example.com", name: "Ada" }, "Passkey");
    expect(setup).not.toHaveBeenCalled();
  });

  it("puts a wrong code under the setup code", async () => {
    const flows = jest.requireMock("@/components/auth/passkey-flows");
    flows.setupTicket.mockRejectedValue(new flows.PasskeyFlowError("Setup is closed or the code is wrong.", 403));
    renderForm();

    fill({ code: "nope", email: "ada@example.com" });
    fireEvent.click(screen.getByRole("button", { name: /create admin with a passkey/i }));

    await waitFor(() => expect(screen.getByLabelText("Setup code")).toHaveAttribute("aria-invalid", "true"));
  });

  it("can use a password instead", () => {
    renderForm();

    fireEvent.click(screen.getByRole("button", { name: /use a password instead/i }));
    expect(screen.getByLabelText("Password")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /create admin account/i })).toBeInTheDocument();
  });
});
