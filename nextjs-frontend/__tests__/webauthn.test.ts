import { creationOptions, deviceName, fromB64url, passkeyErrorMessage, requestOptions, toB64url } from "@/lib/auth/webauthn";

describe("passkey helpers", () => {
  it("round-trips base64url without padding", () => {
    const bytes = new Uint8Array([0, 1, 250, 251, 252, 253, 254, 255]);
    const s = toB64url(bytes)!;
    expect(s).toBe("AAH6-_z9_v8");
    expect(new Uint8Array(fromB64url(s))).toEqual(bytes);
  });

  it("turns the API's creation options into what the browser takes", () => {
    const o = creationOptions({
      rp: { id: "localhost", name: "Lens" },
      user: { id: "AQID", name: "ada@x.io", displayName: "Ada" },
      challenge: "BAUG",
      pubKeyCredParams: [{ type: "public-key", alg: -7 }],
      excludeCredentials: [{ id: "BwgJ", type: "public-key", transports: ["internal"] }],
    });
    expect(new Uint8Array(o.challenge as ArrayBuffer)).toEqual(new Uint8Array([4, 5, 6]));
    expect(new Uint8Array(o.user.id as ArrayBuffer)).toEqual(new Uint8Array([1, 2, 3]));
    expect(new Uint8Array(o.excludeCredentials![0].id as ArrayBuffer)).toEqual(new Uint8Array([7, 8, 9]));
    expect(o.rp.id).toBe("localhost");
  });

  it("turns request options for signing in", () => {
    const o = requestOptions({ challenge: "BAUG", rpId: "lens.example.com", userVerification: "required" });
    expect(o.rpId).toBe("lens.example.com");
    expect(o.allowCredentials).toEqual([]);
  });

  it("says what went wrong in words", () => {
    expect(passkeyErrorMessage(new DOMException("x", "NotAllowedError"))).toMatch(/closed or timed out/);
    expect(passkeyErrorMessage(new DOMException("x", "InvalidStateError"), true)).toMatch(/already has a passkey/);
  });

  it("suggests a name from the device", () => {
    expect(deviceName("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)")).toBe("iPhone");
    expect(deviceName("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0)")).toBe("Mac");
    expect(deviceName("Mozilla/5.0 (Linux; Android 15)")).toBe("Android phone");
  });
});
