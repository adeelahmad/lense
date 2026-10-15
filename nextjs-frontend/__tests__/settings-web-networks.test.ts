import { crossErrors, FIELDS, parse } from "@/components/settings/model";

const spec = FIELDS.find((f) => f.section === "documents" && f.key === "web_networks")!;

describe("private networks Lens may fetch from", () => {
  it("is a list in Settings → Documents, one network per line", () => {
    expect(spec.kind).toBe("lines");
    expect(parse(spec, "192.168.1.0/24\n\nfd00::/8\n")).toEqual({ value: ["192.168.1.0/24", "fd00::/8"] });
  });

  it("says which line isn't a network", () => {
    expect(crossErrors({ "documents.web_networks": ["192.168.1.0/24"] })["documents.web_networks"]).toBeUndefined();
    expect(crossErrors({ "documents.web_networks": ["intranet"] })["documents.web_networks"]).toMatch(
      /“intranet” isn’t a network/,
    );
  });
});
