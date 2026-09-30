import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { login } from "@/components/actions/login-action";
import { LoginForm } from "@/components/auth/login-form";

jest.mock("@/components/actions/login-action", () => ({ login: jest.fn() }));

function fillAndSubmit() {
  fireEvent.change(screen.getByLabelText("Email"), {
    target: { value: "me@example.com" },
  });
  fireEvent.change(screen.getByLabelText("Password"), {
    target: { value: "secret" },
  });
  fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
}

describe("LoginForm", () => {
  it("renders the fields and no setup link by default", () => {
    render(<LoginForm />);

    expect(screen.getByLabelText("Email")).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /forgot your password/i }),
    ).toHaveAttribute("href", "/password-recovery");
    expect(
      screen.queryByRole("link", { name: /set up/i }),
    ).not.toBeInTheDocument();
  });

  it("links to setup when the archive has no admin yet", () => {
    render(<LoginForm setupRequired />);

    expect(
      screen.getByRole("link", { name: /set up this archive/i }),
    ).toHaveAttribute("href", "/setup");
  });

  it("submits the credentials with the callback URL", async () => {
    (login as jest.Mock).mockResolvedValue(undefined);
    render(<LoginForm callbackUrl="/speakers" />);

    fillAndSubmit();

    await waitFor(() => {
      const expected = new FormData();
      expected.set("callbackUrl", "/speakers");
      expected.set("email", "me@example.com");
      expected.set("password", "secret");
      expect(login).toHaveBeenCalledWith(undefined, expected);
    });
  });

  it("shows the server's error", async () => {
    (login as jest.Mock).mockResolvedValue({
      server_validation_error: "Wrong email or password.",
    });
    render(<LoginForm />);

    fillAndSubmit();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Wrong email or password.",
    );
  });

  it("marks invalid fields", async () => {
    (login as jest.Mock).mockResolvedValue({
      errors: { email: ["Enter a valid email address."] },
    });
    render(<LoginForm />);

    fillAndSubmit();

    await waitFor(() =>
      expect(screen.getByLabelText("Email")).toHaveAttribute(
        "aria-invalid",
        "true",
      ),
    );
    expect(screen.getByLabelText("Email")).toHaveAccessibleDescription(
      "Enter a valid email address.",
    );
  });

  it("shows a notice", () => {
    render(<LoginForm notice="Your password was changed." />);

    expect(screen.getByRole("status")).toHaveTextContent(
      "Your password was changed.",
    );
  });
});
