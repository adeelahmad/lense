import { isActive, navFor } from "@/components/app-shell/nav-config";
import { safeCallbackUrl } from "@/lib/definitions";

describe("navigation", () => {
  const labels = (admin: boolean) => navFor(admin).map((i) => i.label);

  it("shows Admin to admins only, and Settings (where Speakers are) to everyone", () => {
    expect(labels(true)).toEqual(expect.arrayContaining(["Settings", "Admin"]));
    expect(labels(false)).toContain("Settings");
    expect(labels(false)).not.toContain("Admin");
    expect(labels(true)).not.toContain("Speakers");
    // Admins see every source as a sensor; members keep Sources, the watched folders feeding their namespaces.
    expect(labels(true)).toContain("Sensors");
    expect(labels(true)).not.toContain("Sources");
    expect(labels(false)).not.toContain("Sensors");
    expect(labels(false)).toEqual(
      expect.arrayContaining(["Home", "Library", "Search", "Chat", "Graph", "Reports", "Pipelines", "Sources"]),
    );
  });

  it("matches the active section", () => {
    expect(isActive("/", "/")).toBe(true);
    expect(isActive("/library", "/")).toBe(false);
    expect(isActive("/resources/12", "/library")).toBe(true);
    expect(isActive("/recordings/12", "/library")).toBe(true); // the old address, on its way to /resources
    expect(isActive("/speakers/4", "/settings")).toBe(true); // a speaker's profile, under Settings
    expect(isActive("/settings/speakers", "/settings")).toBe(true);
    expect(isActive("/speakersx", "/settings")).toBe(false);
    expect(isActive("/sources", "/sensors")).toBe(true); // files, email and calendars: a tab of Sensors
    expect(isActive("/sensors/4", "/sensors")).toBe(true);
  });
});

describe("safeCallbackUrl", () => {
  it.each([
    ["/search?q=1", "/search?q=1"],
    ["//evil.example", "/"],
    ["/\\evil.example", "/"],
    ["https://evil.example", "/"],
    ["/\t/evil.example", "/"],
    ["/\n/evil.example", "/"],
    ["/\r/evil.example", "/"],
    [null, "/"],
  ])("%p -> %p", (input, expected) => {
    expect(safeCallbackUrl(input)).toBe(expected);
  });
});
