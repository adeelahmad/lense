import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { setup } from "@/components/actions/setup-action";
import { SetupForm } from "@/components/auth/setup-form";

jest.mock("@/components/actions/setup-action", () => ({ setup: jest.fn() }));

describe("SetupForm", () => {
  it("submits the first admin's details", async () => {
    (setup as jest.Mock).mockResolvedValue(undefined);
    render(<SetupForm />);

    fireEvent.change(screen.getByLabelText("Setup code"), {
      target: { value: "c0de" },
    });
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Ada" },
    });
    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "ada@example.com" },
    });
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "long enough pw" },
    });
    fireEvent.change(screen.getByLabelText("Confirm password"), {
      target: { value: "long enough pw" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: /create administrator/i }),
    );

    await waitFor(() => {
      const expected = new FormData();
      expected.set("code", "c0de");
      expected.set("name", "Ada");
      expected.set("email", "ada@example.com");
      expected.set("password", "long enough pw");
      expected.set("passwordConfirm", "long enough pw");
      expect(setup).toHaveBeenCalledWith(undefined, expected);
    });
  });

  it("shows field and form errors", async () => {
    (setup as jest.Mock).mockResolvedValue({
      errors: { password: ["Use at least 10 characters."] },
      server_validation_error: "setup is closed or the code is wrong",
    });
    render(<SetupForm />);

    fireEvent.click(
      screen.getByRole("button", { name: /create administrator/i }),
    );

    expect(
      await screen.findByText("Use at least 10 characters."),
    ).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "setup is closed or the code is wrong",
    );
  });
});
