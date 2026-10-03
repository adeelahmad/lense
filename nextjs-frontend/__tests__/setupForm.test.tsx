import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { SetupForm } from "@/components/auth/setup-form";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/components/auth/passkey-flows", () => {
  class PasskeyFlowError extends Error {
    constructor(
      message: string,
      public status?: number,
    ) {
      super(message);
    }
  }
  return { setupTicket: jest.fn(), setupWithoutPasskeyTicket: jest.fn(), finishSignIn: jest.fn(), PasskeyFlowError };
});

const flows = jest.requireMock("@/components/auth/passkey-flows");

function renderForm(initialCode?: string) {
  return render(
    <TooltipProvider>
      <SetupForm initialCode={initialCode} />
    </TooltipProvider>,
  );
}

function fill(values: { code?: string; name?: string; email?: string }) {
  for (const [label, value] of [
    ["Setup code", values.code],
    ["Name", values.name],
    ["Email", values.email],
  ] as const) {
    if (value !== undefined) fireEvent.change(screen.getByLabelText(label), { target: { value } });
  }
}

function secure(on: boolean) {
  Object.defineProperty(window, "isSecureContext", { configurable: true, value: on });
  if (on)
    (window as unknown as { PublicKeyCredential: unknown }).PublicKeyCredential = function PublicKeyCredential() {};
  else delete (window as unknown as { PublicKeyCredential?: unknown }).PublicKeyCredential;
}

afterEach(() => {
  secure(false);
  jest.clearAllMocks();
});

describe("SetupForm", () => {
  it("fills in the code from the setup link and starts at the name", () => {
    renderForm("fr0m-link");

    expect(screen.getByLabelText("Setup code")).toHaveValue("fr0m-link");
    expect(screen.getByLabelText("Name")).toHaveFocus();
    expect(screen.getByText("The setup code from your link is filled in.")).toBeInTheDocument();
    expect(screen.queryByText("make setup-code")).not.toBeInTheDocument();
  });

  it("without a link, offers the command to copy and starts at the code", () => {
    renderForm();

    expect(screen.getByText("make setup-code")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy the command" })).toBeInTheDocument();
    expect(screen.getByLabelText("Setup code")).toHaveFocus();
  });

  it("never asks for a password", () => {
    secure(true);
    renderForm();
    expect(screen.queryByLabelText(/password/i)).not.toBeInTheDocument();
  });

  it("makes the first admin with a passkey", async () => {
    secure(true);
    flows.setupTicket.mockResolvedValue("lt_ticket");
    renderForm();

    fill({ code: "c0de", name: "Ada", email: "ada@example.com" });
    fireEvent.click(screen.getByRole("button", { name: /create admin with a passkey/i }));

    await waitFor(() => expect(flows.finishSignIn).toHaveBeenCalledWith("lt_ticket", "/welcome"));
    expect(flows.setupTicket).toHaveBeenCalledWith({ code: "c0de", email: "ada@example.com", name: "Ada" }, "Passkey");
  });

  it("puts a wrong code under the setup code", async () => {
    secure(true);
    flows.setupTicket.mockRejectedValue(new flows.PasskeyFlowError("Setup is closed or the code is wrong.", 403));
    renderForm();

    fill({ code: "nope", email: "ada@example.com" });
    fireEvent.click(screen.getByRole("button", { name: /create admin with a passkey/i }));

    await waitFor(() => expect(screen.getByLabelText("Setup code")).toHaveAttribute("aria-invalid", "true"));
    fill({ code: "c0de2" });
    expect(screen.getByLabelText("Setup code")).not.toHaveAttribute("aria-invalid");
  });

  it("on a plain http:// address, sets up with the code alone", async () => {
    flows.setupWithoutPasskeyTicket.mockResolvedValue("lt_ticket");
    renderForm();

    expect(screen.getByText(/sign in later with a passkey at the https:\/\/ address/i)).toBeInTheDocument();
    fill({ code: "c0de", email: "ada@example.com" });
    fireEvent.click(screen.getByRole("button", { name: /create admin account/i }));

    await waitFor(() => expect(flows.finishSignIn).toHaveBeenCalledWith("lt_ticket", "/welcome"));
    expect(flows.setupWithoutPasskeyTicket).toHaveBeenCalledWith({ code: "c0de", email: "ada@example.com" });
  });

  it("waits for the code and an email", () => {
    secure(true);
    renderForm();
    fill({ code: "c0de", email: "not-an-email" });
    expect(screen.getByRole("button", { name: /create admin with a passkey/i })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
  });

  it("fills in the code from the setup link and starts at the name", () => {
    renderForm("fr0m-link");
    expect(screen.getByLabelText("Setup code")).toHaveValue("fr0m-link");
    expect(screen.getByLabelText("Name")).toHaveFocus();
  });
});
