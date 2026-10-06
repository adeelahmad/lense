/** @jest-environment node */
// eslint-disable-next-line @typescript-eslint/no-require-imports
const { scrubForwardedFor } = require("../lens-server.js");

describe("scrubForwardedFor", () => {
  const spoofed = () => ({ host: "lens.example", "x-forwarded-for": "10.9.8.7" });

  it("drops a visitor's own X-Forwarded-For, so Next fills in their real address", () => {
    expect(scrubForwardedFor(spoofed(), "203.0.113.5", false, new Set())).toEqual({ host: "lens.example" });
  });

  it("keeps it from a proxy known to set it", () => {
    const tunnel = new Set(["172.18.0.4"]);
    expect(scrubForwardedFor(spoofed(), "::ffff:172.18.0.4", false, tunnel)["x-forwarded-for"]).toBe("10.9.8.7");
    expect(scrubForwardedFor(spoofed(), "203.0.113.5", true, new Set())["x-forwarded-for"]).toBe("10.9.8.7");
  });
});
