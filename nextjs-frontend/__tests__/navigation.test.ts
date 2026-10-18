import { isActive, navFor } from "@/components/app-shell/nav-config";
import { safeCallbackUrl } from "@/lib/definitions";

describe("navigation", () => {
  const labels = (admin: boolean) => navFor(admin).map((i) => i.label);

  it("shows Admin to admins only, and Settings (where People are) to everyone", () => {
    expect(labels(true)).toEqual(expect.arrayContaining(["Settings", "Admin"]));
    expect(labels(false)).toContain("Settings");
    expect(labels(false)).not.toContain("Admin");
    expect(labels(true)).toContain("Analytics");
    expect(labels(false)).not.toContain("Analytics");
    expect(labels(true)).not.toContain("People");
    // Admins see every source as a sensor; members keep Sources, the watched folders feeding their namespaces.
    expect(labels(true)).toContain("Sensors");
    expect(labels(true)).not.toContain("Sources");
    expect(labels(false)).not.toContain("Sensors");
    expect(labels(false)).toEqual(
      expect.arrayContaining(["Home", "Library", "Search", "Chat", "Graph", "Reports", "Pipelines", "Sources"]),
    );
    // members make and manage automations in namespaces they edit, so everyone has the page
    expect(labels(true)).toContain("Automations");
    expect(labels(false)).toContain("Automations");
  });

  it("keeps each group together, in order: every day, Explore, Automate, Manage", () => {
    for (const admin of [true, false]) {
      const groups = navFor(admin).map((i) => i.group);
      const order = groups.filter((g, i) => g !== groups[i - 1]);
      expect(order).toEqual(["", "Explore", "Automate", "Manage"]);
    }
    expect(
      navFor(true)
        .filter((i) => i.group === "Manage")
        .map((i) => i.label),
    ).toEqual(["Costs", "Analytics", "Settings", "Admin"]);
  });

  it("has Schedules and Routines open from Automations, so the nav is no longer than before (one phone screen)", () => {
    for (const admin of [true, false]) {
      expect(navFor(admin).find((i) => i.href === "/automations")?.group).toBe("Automate");
      expect(navFor(admin).find((i) => i.href === "/schedules")).toBeUndefined();
      expect(navFor(admin).find((i) => i.href === "/routines")).toBeUndefined();
    }
    expect(isActive("/schedules", "/automations")).toBe(true);
    expect(isActive("/routines", "/automations")).toBe(true);
    expect(isActive("/routines/3", "/automations")).toBe(true);
    expect(isActive("/routines/history", "/automations")).toBe(true);
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
    expect(isActive("/automations/4", "/automations")).toBe(true); // an automation's page
    expect(isActive("/automations", "/approvals")).toBe(false);
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
