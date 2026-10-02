/** @jest-environment node */
import { browserAddress, trustProxyHeaders } from "@/lib/api/backend-proxy";

describe("browserAddress", () => {
  const incoming = new URL("http://127.0.0.1:3000/.well-known/oauth-authorization-server");
  const spoofed = new Headers({ host: "lens.example", "x-forwarded-host": "evil.tld", "x-forwarded-proto": "https" });

  it("ignores a browser's own X-Forwarded-* unless a proxy is trusted", () => {
    expect(browserAddress(spoofed, incoming, false)).toEqual({ host: "lens.example", proto: "http" });
  });

  it("passes on what a trusted reverse proxy reports, its first value", () => {
    const h = new Headers({
      host: "web:3000",
      "x-forwarded-host": "lens.example, inner",
      "x-forwarded-proto": "https",
    });
    expect(browserAddress(h, incoming, true)).toEqual({ host: "lens.example", proto: "https" });
  });

  it("falls back to Host and Next's own address", () => {
    expect(browserAddress(new Headers({ host: "lens.example" }), incoming, true)).toEqual({
      host: "lens.example",
      proto: "http",
    });
    expect(browserAddress(new Headers(), incoming, false)).toEqual({ host: "127.0.0.1:3000", proto: "http" });
  });

  it("trusts proxy headers only when TRUST_PROXY_HEADERS says so", () => {
    const before = process.env.TRUST_PROXY_HEADERS;
    try {
      delete process.env.TRUST_PROXY_HEADERS;
      expect(trustProxyHeaders()).toBe(false);
      process.env.TRUST_PROXY_HEADERS = "true";
      expect(trustProxyHeaders()).toBe(true);
      process.env.TRUST_PROXY_HEADERS = "no";
      expect(trustProxyHeaders()).toBe(false);
    } finally {
      if (before === undefined) delete process.env.TRUST_PROXY_HEADERS;
      else process.env.TRUST_PROXY_HEADERS = before;
    }
  });
});
