import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";
import { notFound } from "next/navigation";

import ConfirmPage from "@/app/(auth)/password-recovery/confirm/page";
import {
  passwordReset,
  passwordResetConfirm,
} from "@/components/actions/password-reset-action";
import { PasswordResetConfirmForm } from "@/components/auth/password-reset-confirm-form";
import { PasswordResetForm } from "@/components/auth/password-reset-form";

jest.mock("next/navigation", () => ({
  notFound: jest.fn(() => {
    throw new Error("NEXT_NOT_FOUND");
  }),
}));
jest.mock("@/components/actions/password-reset-action", () => ({
  passwordReset: jest.fn(),
  passwordResetConfirm: jest.fn(),
}));

describe("PasswordResetForm", () => {
  it("submits the email and shows the confirmation", async () => {
    (passwordReset as jest.Mock).mockResolvedValue({
      message: "A reset link is on its way.",
    });
    render(<PasswordResetForm />);

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "a@a.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: /send reset link/i }));

    expect(await screen.findByRole("status")).toHaveTextContent(
      "A reset link is on its way.",
    );
    const expected = new FormData();
    expected.set("email", "a@a.com");
    expect(passwordReset).toHaveBeenCalledWith(undefined, expected);
  });
});

describe("PasswordResetConfirmForm", () => {
  it("submits the token with the new password", async () => {
    (passwordResetConfirm as jest.Mock).mockResolvedValue(undefined);
    render(<PasswordResetConfirmForm token="tok" />);

    fireEvent.change(screen.getByLabelText("New password"), {
      target: { value: "long enough pw" },
    });
    fireEvent.change(screen.getByLabelText("Confirm password"), {
      target: { value: "long enough pw" },
    });
    fireEvent.click(screen.getByRole("button", { name: /change password/i }));

    await waitFor(() => {
      const expected = new FormData();
      expected.set("token", "tok");
      expected.set("password", "long enough pw");
      expected.set("passwordConfirm", "long enough pw");
      expect(passwordResetConfirm).toHaveBeenCalledWith(undefined, expected);
    });
  });

  it("shows the server's error", async () => {
    (passwordResetConfirm as jest.Mock).mockResolvedValue({
      server_validation_error: "the reset link is invalid or has expired",
    });
    render(<PasswordResetConfirmForm token="tok" />);

    fireEvent.click(screen.getByRole("button", { name: /change password/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "invalid or has expired",
    );
  });
});

describe("password reset confirm page", () => {
  it("is not found without a token", async () => {
    await expect(
      ConfirmPage({ searchParams: Promise.resolve({}) }),
    ).rejects.toThrow("NEXT_NOT_FOUND");
    expect(notFound).toHaveBeenCalled();
  });

  it("renders the form with the token", async () => {
    render(
      await ConfirmPage({ searchParams: Promise.resolve({ token: "tok" }) }),
    );

    expect(screen.getByLabelText("New password")).toBeInTheDocument();
  });
});
