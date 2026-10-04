/**
 * @jest-environment node
 */
import { NextRequest } from "next/server";

import { atBrowserOrigin } from "@/lib/auth/browser-origin";

const at = (headers: Record<string, string>) =>
  atBrowserOrigin(new NextRequest("http://localhost:3000/api/auth/signout?x=1", { headers })).nextUrl.href;

describe("sign-in routes follow the browser's address", () => {
  const env = process.env;
  beforeEach(() => {
    process.env = { ...env };
    delete process.env.AUTH_URL;
    delete process.env.NEXTAUTH_URL;
    delete process.env.TRUST_PROXY_HEADERS;
  });
  afterAll(() => (process.env = env));

  it("uses the Host header, not the address Next listens on", () => {
    expect(at({ host: "nas.local:3000" })).toBe("http://nas.local:3000/api/auth/signout?x=1");
    expect(at({ host: "localhost:3000" })).toBe("http://localhost:3000/api/auth/signout?x=1");
  });

  it("is https through a tunnel or proxy that says so", () => {
    expect(at({ host: "tiny-blue-fox.trycloudflare.com", "x-forwarded-proto": "https" })).toBe(
      "https://tiny-blue-fox.trycloudflare.com/api/auth/signout?x=1",
    );
  });

  it("ignores X-Forwarded-Host unless a proxy in front is trusted", () => {
    expect(at({ host: "nas.local:3000", "x-forwarded-host": "evil.example" })).toContain("//nas.local:3000/");
    process.env.TRUST_PROXY_HEADERS = "true";
    expect(at({ host: "frontend:3000", "x-forwarded-host": "lens.example.com", "x-forwarded-proto": "https" })).toBe(
      "https://lens.example.com/api/auth/signout?x=1",
    );
  });

  it("leaves a malformed host, and a pinned AUTH_URL, alone", () => {
    expect(at({ host: "bad host/x" })).toBe("http://localhost:3000/api/auth/signout?x=1");
    process.env.AUTH_URL = "https://lens.example.com";
    expect(at({ host: "nas.local:3000" })).toBe("http://localhost:3000/api/auth/signout?x=1");
  });
});
