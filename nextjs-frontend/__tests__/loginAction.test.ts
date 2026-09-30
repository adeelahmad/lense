import { login } from "@/components/actions/login-action";
import { signIn } from "@/auth";

jest.mock("next-auth", () => {
  class AuthError extends Error {}
  class CredentialsSignin extends AuthError {
    code = "credentials";
  }
  return { AuthError, CredentialsSignin };
});

jest.mock("@/auth", () => ({ signIn: jest.fn() }));

// eslint-disable-next-line @typescript-eslint/no-require-imports
const { AuthError, CredentialsSignin } = require("next-auth");

function form(values: Record<string, string>) {
  const data = new FormData();
  for (const [k, v] of Object.entries(values)) data.set(k, v);
  return data;
}

describe("login action", () => {
  it("signs in with the credentials and a safe callback URL", async () => {
    (signIn as jest.Mock).mockResolvedValue(undefined);

    await login(
      undefined,
      form({
        email: "a@a.com",
        password: "secret",
        callbackUrl: "/search?q=x",
      }),
    );

    expect(signIn).toHaveBeenCalledWith("credentials", {
      email: "a@a.com",
      password: "secret",
      redirectTo: "/search?q=x",
    });
  });

  it("ignores off-site callback URLs", async () => {
    await login(
      undefined,
      form({
        email: "a@a.com",
        password: "secret",
        callbackUrl: "//evil.example",
      }),
    );

    expect(signIn).toHaveBeenCalledWith(
      "credentials",
      expect.objectContaining({ redirectTo: "/" }),
    );
  });

  it("validates the fields before calling the backend", async () => {
    const result = await login(
      undefined,
      form({ email: "nope", password: "" }),
    );

    expect(signIn).not.toHaveBeenCalled();
    expect(result).toEqual({
      errors: {
        email: ["Enter a valid email address."],
        password: ["Enter your password."],
      },
    });
  });

  it("reports wrong credentials", async () => {
    (signIn as jest.Mock).mockRejectedValue(new CredentialsSignin());

    const result = await login(
      undefined,
      form({ email: "a@a.com", password: "bad" }),
    );

    expect(result).toEqual({
      server_validation_error: "Wrong email or password.",
    });
  });

  it("reports throttling", async () => {
    const err = new CredentialsSignin();
    err.code = "throttled";
    (signIn as jest.Mock).mockRejectedValue(err);

    const result = await login(
      undefined,
      form({ email: "a@a.com", password: "bad" }),
    );

    expect(result).toEqual({
      server_validation_error: "Too many attempts; try again in a few minutes.",
      throttled: true,
    });
  });

  it("reports other auth failures as unexpected", async () => {
    jest.spyOn(console, "error").mockImplementation(() => {});
    (signIn as jest.Mock).mockRejectedValue(new AuthError("boom"));

    const result = await login(
      undefined,
      form({ email: "a@a.com", password: "x" }),
    );

    expect(result).toEqual({
      server_error: "An unexpected error occurred. Please try again later.",
    });
  });

  it("rethrows the redirect thrown by a successful sign-in", async () => {
    const redirect = new Error("NEXT_REDIRECT");
    (signIn as jest.Mock).mockRejectedValue(redirect);

    await expect(
      login(undefined, form({ email: "a@a.com", password: "x" })),
    ).rejects.toBe(redirect);
  });
});
