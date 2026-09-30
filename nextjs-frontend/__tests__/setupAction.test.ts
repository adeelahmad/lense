import { Auth } from "@/app/openapi-client";
import { signIn } from "@/auth";
import { setup } from "@/components/actions/setup-action";
import { endBackendSession } from "@/lib/auth/tokens";

jest.mock("@/app/openapi-client", () => ({ Auth: { setup: jest.fn() } }));
jest.mock("@/auth", () => ({ signIn: jest.fn() }));
jest.mock("@/lib/auth/tokens", () => ({ endBackendSession: jest.fn() }));

function form(values: Record<string, string>) {
  const data = new FormData();
  for (const [k, v] of Object.entries(values)) data.set(k, v);
  return data;
}

const valid = {
  code: "abc123",
  name: "Ada",
  email: "ada@example.com",
  password: "long enough pw",
  passwordConfirm: "long enough pw",
};

describe("setup action", () => {
  it("creates the admin, ends the setup session and signs in", async () => {
    (Auth.setup as jest.Mock).mockResolvedValue({
      data: { refresh_token: "r1" },
    });

    await setup(undefined, form(valid));

    expect(Auth.setup).toHaveBeenCalledWith({
      client: expect.anything(),
      body: {
        code: "abc123",
        name: "Ada",
        email: "ada@example.com",
        password: "long enough pw",
      },
    });
    expect(endBackendSession).toHaveBeenCalledWith("r1");
    expect(signIn).toHaveBeenCalledWith("credentials", {
      email: "ada@example.com",
      password: "long enough pw",
      redirectTo: "/",
    });
  });

  it("validates the form", async () => {
    const result = await setup(
      undefined,
      form({ ...valid, code: "", password: "short", passwordConfirm: "other" }),
    );

    expect(Auth.setup).not.toHaveBeenCalled();
    expect(result).toEqual({
      errors: {
        code: ["Enter the setup code from the server log."],
        password: ["Use at least 10 characters."],
        passwordConfirm: ["Passwords must match."],
      },
    });
  });

  it("checks that the passwords match", async () => {
    const result = await setup(
      undefined,
      form({ ...valid, passwordConfirm: "something else" }),
    );

    expect(result).toEqual({
      errors: { passwordConfirm: ["Passwords must match."] },
    });
  });

  it("shows the backend's refusal", async () => {
    (Auth.setup as jest.Mock).mockResolvedValue({
      error: { detail: "setup is closed or the code is wrong" },
    });

    const result = await setup(undefined, form(valid));

    expect(result).toEqual({
      server_validation_error: "setup is closed or the code is wrong",
    });
    expect(signIn).not.toHaveBeenCalled();
  });

  it("handles network failures", async () => {
    jest.spyOn(console, "error").mockImplementation(() => {});
    (Auth.setup as jest.Mock).mockRejectedValue(new Error("offline"));

    const result = await setup(undefined, form(valid));

    expect(result).toEqual({
      server_error: "An unexpected error occurred. Please try again later.",
    });
  });
});
