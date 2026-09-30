import { Auth } from "@/app/openapi-client";
import { passwordReset, passwordResetConfirm } from "@/components/actions/password-reset-action";
import { redirect } from "next/navigation";

jest.mock("next/navigation", () => ({ redirect: jest.fn() }));
jest.mock("@/app/openapi-client", () => ({
  Auth: { forgotPassword: jest.fn(), resetPassword: jest.fn() },
}));

function form(values: Record<string, string>) {
  const data = new FormData();
  for (const [k, v] of Object.entries(values)) data.set(k, v);
  return data;
}

const UNEXPECTED = {
  server_error: "An unexpected error occurred. Please try again later.",
};

describe("passwordReset action", () => {
  it("asks the backend to email a reset link", async () => {
    (Auth.forgotPassword as jest.Mock).mockResolvedValue({
      data: { ok: true },
    });

    const result = await passwordReset(undefined, form({ email: "a@a.com" }));

    expect(Auth.forgotPassword).toHaveBeenCalledWith({
      client: expect.anything(),
      body: { email: "a@a.com" },
    });
    expect(result).toEqual({
      message: "If that address has an account, a reset link is on its way.",
    });
  });

  it("validates the email", async () => {
    const result = await passwordReset(undefined, form({ email: "nope" }));

    expect(Auth.forgotPassword).not.toHaveBeenCalled();
    expect(result).toEqual({
      errors: { email: ["Enter a valid email address."] },
    });
  });

  it("handles network failures", async () => {
    jest.spyOn(console, "error").mockImplementation(() => {});
    (Auth.forgotPassword as jest.Mock).mockRejectedValue(new Error("offline"));

    expect(await passwordReset(undefined, form({ email: "a@a.com" }))).toEqual(UNEXPECTED);
  });
});

describe("passwordResetConfirm action", () => {
  const valid = {
    token: "tok",
    password: "long enough pw",
    passwordConfirm: "long enough pw",
  };

  it("sets the new password and goes to sign-in", async () => {
    (Auth.resetPassword as jest.Mock).mockResolvedValue({ data: { ok: true } });

    await passwordResetConfirm(undefined, form(valid));

    expect(Auth.resetPassword).toHaveBeenCalledWith({
      client: expect.anything(),
      body: { token: "tok", password: "long enough pw" },
    });
    expect(redirect).toHaveBeenCalledWith("/login?reset=1");
  });

  it("shows the backend's error for a bad token", async () => {
    (Auth.resetPassword as jest.Mock).mockResolvedValue({
      error: { detail: "the reset link is invalid or has expired" },
    });

    const result = await passwordResetConfirm(undefined, form(valid));

    expect(result).toEqual({
      server_validation_error: "the reset link is invalid or has expired",
    });
    expect(redirect).not.toHaveBeenCalled();
  });

  it("formats FastAPI validation errors", async () => {
    (Auth.resetPassword as jest.Mock).mockResolvedValue({
      error: {
        detail: [{ loc: ["body", "password"], msg: "too weak", type: "value_error" }],
      },
    });

    const result = await passwordResetConfirm(undefined, form(valid));

    expect(result).toEqual({ server_validation_error: "too weak" });
  });

  it("validates the passwords", async () => {
    const result = await passwordResetConfirm(
      undefined,
      form({ token: "tok", password: "short", passwordConfirm: "different" }),
    );

    expect(Auth.resetPassword).not.toHaveBeenCalled();
    expect(result).toEqual({
      errors: {
        password: ["Use at least 10 characters."],
        passwordConfirm: ["Passwords must match."],
      },
    });
  });

  it("handles network failures", async () => {
    jest.spyOn(console, "error").mockImplementation(() => {});
    (Auth.resetPassword as jest.Mock).mockRejectedValue(new Error("offline"));

    expect(await passwordResetConfirm(undefined, form(valid))).toEqual(UNEXPECTED);
  });
});
