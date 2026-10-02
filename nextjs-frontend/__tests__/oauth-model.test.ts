import { ACCESS_LABEL, appHost, canWrite, consentRequest, returnsTo, safeToFollow } from "@/components/oauth/model";

describe("the consent request in the page's query", () => {
  it("reads what the app asked for", () => {
    expect(
      consentRequest({
        client_id: "lc_1",
        redirect_uri: "https://app.example/cb",
        code_challenge: "c".repeat(43),
        code_challenge_method: "S256",
        scope: "read write",
        state: ["s1", "s2"],
      }),
    ).toEqual({
      client_id: "lc_1",
      redirect_uri: "https://app.example/cb",
      response_type: "code",
      code_challenge: "c".repeat(43),
      code_challenge_method: "S256",
      scope: "read write",
      state: "s1",
      resource: undefined,
    });
  });

  it("is nothing without an app or an address to return to", () => {
    expect(consentRequest({ redirect_uri: "https://app.example/cb" })).toBeNull();
    expect(consentRequest({ client_id: "lc_1" })).toBeNull();
    expect(consentRequest({ client_id: "lc_1", redirect_uri: "x" })?.code_challenge).toBe("");
  });
});

describe("where the browser may be sent", () => {
  it("follows https, this machine and an app's own scheme", () => {
    expect(safeToFollow("https://app.example/cb?code=1")).toBe(true);
    expect(safeToFollow("http://127.0.0.1:7777/cb?code=1")).toBe(true);
    expect(safeToFollow("http://localhost/cb")).toBe(true);
    expect(safeToFollow("cursor://anysphere.cursor/oauth?code=1")).toBe(true);
  });

  it("never follows addresses the browser or the system would run", () => {
    for (const bad of [
      "javascript:alert(1)",
      "data:text/html,x",
      "ms-msdt:/id",
      "intent://x",
      "http://example.org/cb",
      "https://user:pw@app.example/cb",
      "http://evil.example\\@localhost/cb",
      "not a url",
    ])
      expect(safeToFollow(bad)).toBe(false);
  });

  it("says where an app returns to in words people know", () => {
    expect(returnsTo("https://app.example/cb")).toBe("app.example");
    expect(returnsTo("http://127.0.0.1:7777/cb")).toBe("an app on this computer");
    expect(returnsTo("http://example.org/cb")).toBe("example.org");
    expect(returnsTo("cursor://x/y")).toBe("an app on this device (cursor://)");
    expect(returnsTo("nonsense")).toBe("nonsense");
  });
});

describe("what an app may do", () => {
  it("labels scopes", () => {
    expect(canWrite("read write")).toBe(true);
    expect(canWrite("read")).toBe(false);
    expect(canWrite(null)).toBe(false);
    expect(ACCESS_LABEL("read write")).toBe("Read & write");
    expect(ACCESS_LABEL("read")).toBe("Read only");
  });

  it("shows an app's site by its host", () => {
    expect(appHost("https://desk.example/about")).toBe("desk.example");
    expect(appHost("nope")).toBeNull();
    expect(appHost(null)).toBeNull();
  });
});
