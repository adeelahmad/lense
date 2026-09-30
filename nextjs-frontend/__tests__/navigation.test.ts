import { isActive, navFor } from "@/components/app-shell/nav-config";
import { safeCallbackUrl } from "@/lib/definitions";

describe("navigation", () => {
  const labels = (admin: boolean) =>
    navFor(admin).flatMap((s) => s.items.map((i) => i.label));

  it("shows Settings to admins only", () => {
    expect(labels(true)).toContain("Settings");
    expect(labels(false)).not.toContain("Settings");
    expect(labels(false)).toEqual(
      expect.arrayContaining(["Library", "Search", "Jobs"]),
    );
  });

  it("matches the active section", () => {
    expect(isActive("/", "/")).toBe(true);
    expect(isActive("/recordings/12", "/")).toBe(false);
    expect(isActive("/recordings/12", "/recordings")).toBe(true);
    expect(isActive("/recordingsx", "/recordings")).toBe(false);
  });
});

describe("safeCallbackUrl", () => {
  it.each([
    ["/search?q=1", "/search?q=1"],
    ["//evil.example", "/"],
    ["/\\evil.example", "/"],
    ["https://evil.example", "/"],
    [null, "/"],
  ])("%p -> %p", (input, expected) => {
    expect(safeCallbackUrl(input)).toBe(expected);
  });
});
