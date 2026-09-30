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
};

describe("setup action", () => {
  it("creates the admin, ends the setup session and signs in", async () => {
    (Auth.setup as jest.Mock).mockResolvedValue({
      data: { refresh_token: "r1" },
      response: { status: 200 },
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
      form({ ...valid, code: "", password: "short" }),
    );

    expect(Auth.setup).not.toHaveBeenCalled();
    expect(result).toEqual({
      errors: {
        code: ["Enter the setup code from the server log."],
        password: ["Use at least 10 characters."],
      },
    });
  });

  it("puts a wrong or closed setup code under the code field", async () => {
    (Auth.setup as jest.Mock).mockResolvedValue({
      error: { detail: "setup is closed or the code is wrong" },
      response: { status: 403 },
    });

    const result = await setup(undefined, form(valid));

    expect(result).toEqual({
      errors: {
        code: [
          "Setup is closed or the code is wrong. Copy the code again from the server log.",
        ],
      },
    });
    expect(signIn).not.toHaveBeenCalled();
  });

  it("puts the backend's email and password refusals under their fields", async () => {
    (Auth.setup as jest.Mock).mockResolvedValue({
      error: { detail: "that email already has an account" },
      response: { status: 400 },
    });
    expect(await setup(undefined, form(valid))).toEqual({
      errors: { email: ["That email already has an account."] },
    });

    (Auth.setup as jest.Mock).mockResolvedValue({
      error: { detail: "passwords need at least 10 characters" },
      response: { status: 400 },
    });
    expect(await setup(undefined, form(valid))).toEqual({
      errors: { password: ["Passwords need at least 10 characters."] },
    });
  });

  it("shows other refusals as a form error", async () => {
    (Auth.setup as jest.Mock).mockResolvedValue({
      error: { detail: "something else" },
      response: { status: 400 },
    });

    expect(await setup(undefined, form(valid))).toEqual({
      server_validation_error: "Something else.",
    });
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
