/**
 * @jest-environment node
 */
import { fetchWithReauth } from "@/lib/api/browser";
import {
  describeHeld,
  discardHeld,
  getReauthState,
  holdUntilSignedIn,
  openSignedOut,
  setSessionRefresher,
  signedInAgain,
  SignedOutError,
} from "@/lib/auth/reauth";

jest.mock("next-auth/react", () => ({ useSession: jest.fn() }));

const ok = (body = "{}") => new Response(body, { status: 200 });
const unauthorized = () => new Response('{"detail":"sign in first"}', { status: 401 });

afterEach(() => {
  setSessionRefresher(null);
  discardHeld();
});

describe("describeHeld", () => {
  it("names what was being done", () => {
    expect(describeHeld([{ method: "GET", url: "/api/v1/recordings" }])).toBeNull();
    expect(describeHeld([{ method: "PUT", url: "http://x/api/v1/settings/server" }])).toBe("saving settings");
    expect(describeHeld([{ method: "PUT", url: "/api/v1/recordings/1/metadata" }])).toBe("saving the metadata");
    expect(
      describeHeld([
        { method: "PUT", url: "/api/v1/settings/server" },
        { method: "DELETE", url: "/api/v1/tokens/3" },
      ]),
    ).toBe("saving your changes");
    expect(describeHeld([{ method: "POST", url: "/api/v1/something/new" }])).toBe("saving your changes");
  });
});

describe("the sign-in gate", () => {
  it("holds requests until the person signs in again, then resolves them all", async () => {
    const a = holdUntilSignedIn({ method: "GET", url: "/a" });
    const b = holdUntilSignedIn({ method: "PUT", url: "/api/v1/settings/llm" });
    expect(getReauthState()).toEqual({
      open: true,
      held: [expect.objectContaining({ url: "/a" }), expect.objectContaining({ url: "/api/v1/settings/llm" })],
    });

    signedInAgain("new-token");

    await expect(a).resolves.toBe("new-token");
    await expect(b).resolves.toBe("new-token");
    expect(getReauthState()).toEqual({ open: false, held: [] });
  });

  it("discards what was held when someone else signs in", async () => {
    const held = holdUntilSignedIn({ method: "POST", url: "/api/v1/tokens" });
    discardHeld();
    await expect(held).rejects.toBeInstanceOf(SignedOutError);
    expect(getReauthState().open).toBe(false);
  });

  it("can open without a held request", () => {
    openSignedOut();
    expect(getReauthState()).toEqual({ open: true, held: [] });
  });
});

describe("fetchWithReauth", () => {
  const realFetch = global.fetch;
  afterEach(() => {
    global.fetch = realFetch;
  });

  it("passes other answers straight through", async () => {
    global.fetch = jest.fn().mockResolvedValue(ok("fine"));
    const res = await fetchWithReauth(
      new Request("http://x/api/v1/me", {
        headers: { Authorization: "Bearer a" },
      }),
    );
    expect(await res.text()).toBe("fine");
    expect(global.fetch).toHaveBeenCalledTimes(1);
  });

  it("doesn't hold requests that weren't signed in", async () => {
    global.fetch = jest.fn().mockResolvedValue(unauthorized());
    const res = await fetchWithReauth(new Request("http://x/api/v1/auth/status"));
    expect(res.status).toBe(401);
    expect(getReauthState().open).toBe(false);
  });

  it("retries with a silently refreshed token", async () => {
    global.fetch = jest.fn().mockResolvedValueOnce(unauthorized()).mockResolvedValueOnce(ok("again"));
    setSessionRefresher(async () => "fresh");

    const res = await fetchWithReauth(
      new Request("http://x/api/v1/users", {
        method: "POST",
        body: '{"a":1}',
        headers: { Authorization: "Bearer stale" },
      }),
    );

    expect(await res.text()).toBe("again");
    const replay = (global.fetch as jest.Mock).mock.calls[1][0] as Request;
    expect(replay.headers.get("Authorization")).toBe("Bearer fresh");
    expect(replay.method).toBe("POST");
    expect(await replay.text()).toBe('{"a":1}');
    expect(getReauthState().open).toBe(false);
  });

  it("holds the request and replays it after signing in again", async () => {
    global.fetch = jest.fn().mockResolvedValueOnce(unauthorized()).mockResolvedValueOnce(ok("replayed"));
    setSessionRefresher(async () => null);

    const pending = fetchWithReauth(
      new Request("http://x/api/v1/settings/llm", {
        method: "PUT",
        body: "{}",
        headers: { Authorization: "Bearer old" },
      }),
    );
    await new Promise((r) => setTimeout(r, 0));
    expect(getReauthState()).toEqual({
      open: true,
      held: [{ method: "PUT", url: "http://x/api/v1/settings/llm" }],
    });

    signedInAgain("after-sign-in");
    const res = await pending;

    expect(await res.text()).toBe("replayed");
    expect(((global.fetch as jest.Mock).mock.calls[1][0] as Request).headers.get("Authorization")).toBe(
      "Bearer after-sign-in",
    );
  });

  it("doesn't reuse the same token as a refresh", async () => {
    global.fetch = jest.fn().mockResolvedValueOnce(unauthorized()).mockResolvedValueOnce(ok());
    setSessionRefresher(async () => "same");

    const pending = fetchWithReauth(
      new Request("http://x/api/v1/me", {
        headers: { Authorization: "Bearer same" },
      }),
    );
    await new Promise((r) => setTimeout(r, 0));
    expect(getReauthState().open).toBe(true);
    signedInAgain("new");
    await pending;
  });
});
