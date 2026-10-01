import { isActive, navFor } from "@/components/app-shell/nav-config";
import { safeCallbackUrl } from "@/lib/definitions";

describe("navigation", () => {
  const labels = (admin: boolean) => navFor(admin).map((i) => i.label);

  it("shows Settings and Admin to admins only", () => {
    expect(labels(true)).toEqual(expect.arrayContaining(["Settings", "Admin"]));
    expect(labels(false)).not.toContain("Settings");
    expect(labels(false)).not.toContain("Admin");
    expect(labels(false)).toEqual(
      expect.arrayContaining([
        "Home",
        "Library",
        "Search",
        "Chat",
        "Speakers",
        "Graph",
        "Reports",
        "Pipelines",
        "Sources",
      ]),
    );
  });

  it("matches the active section", () => {
    expect(isActive("/", "/")).toBe(true);
    expect(isActive("/library", "/")).toBe(false);
    expect(isActive("/resources/12", "/library")).toBe(true);
    expect(isActive("/recordings/12", "/library")).toBe(true); // the old address, on its way to /resources
    expect(isActive("/speakers/4", "/speakers")).toBe(true);
    expect(isActive("/speakersx", "/speakers")).toBe(false);
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
