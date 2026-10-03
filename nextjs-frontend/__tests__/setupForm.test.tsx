import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { setup } from "@/components/actions/setup-action";
import { SetupForm } from "@/components/auth/setup-form";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/components/actions/setup-action", () => ({ setup: jest.fn() }));

function renderForm(initialCode?: string) {
  return render(
    <TooltipProvider>
      <SetupForm initialCode={initialCode} />
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
