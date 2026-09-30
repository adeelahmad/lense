import { Auth } from "@/app/openapi-client";
import {
  needsRefresh,
  refreshTokens,
  sessionUser,
  tokensFromPair,
} from "@/lib/auth/tokens";

jest.mock("@/app/openapi-client", () => ({
  Auth: { refresh: jest.fn(), logout: jest.fn() },
}));

const pair = {
  access_token: "a2",
  refresh_token: "r2",
  token_type: "bearer" as const,
  expires_in: 900,
  user: { id: 7, email: "a@a.com", name: null, admin: true },
};

describe("session tokens", () => {
  it("converts a token pair", () => {
    expect(tokensFromPair(pair, 1_000)).toEqual({
      accessToken: "a2",
      refreshToken: "r2",
      expiresAt: 901_000,
    });
    expect(sessionUser(pair.user)).toEqual({
      id: "7",
      email: "a@a.com",
      name: null,
      admin: true,
    });
  });

  it("refreshes a minute before expiry", () => {
    expect(needsRefresh(100_000, 30_000)).toBe(false);
    expect(needsRefresh(100_000, 40_000)).toBe(true);
    expect(needsRefresh(100_000, 200_000)).toBe(true);
  });

  it("shares one backend call between concurrent refreshes of the same token", async () => {
    (Auth.refresh as jest.Mock).mockResolvedValue({
      data: pair,
      response: { status: 200 },
    });

    const [a, b] = await Promise.all([
      refreshTokens("shared"),
      refreshTokens("shared"),
    ]);
    const later = await refreshTokens("shared");

    expect(a).toBe(pair);
    expect(b).toBe(pair);
    expect(later).toBe(pair);
    expect(Auth.refresh).toHaveBeenCalledTimes(1);
    expect(Auth.refresh).toHaveBeenCalledWith({
      client: expect.anything(),
      body: { refresh_token: "shared" },
    });
  });

  it("resolves to null when the session has ended", async () => {
    (Auth.refresh as jest.Mock).mockResolvedValue({
      error: {},
      response: { status: 401 },
    });

    await expect(refreshTokens("ended")).resolves.toBeNull();
  });

  it("rejects on transient failures and retries next time", async () => {
    (Auth.refresh as jest.Mock)
      .mockResolvedValueOnce({ error: {}, response: { status: 503 } })
      .mockResolvedValueOnce({ data: pair, response: { status: 200 } });

    await expect(refreshTokens("flaky")).rejects.toThrow("503");
    await expect(refreshTokens("flaky")).resolves.toBe(pair);
  });
});
