import {
  ALL_PARTS,
  accessLabel,
  accessPatch,
  accessSummary,
  ipGroupOpens,
  partsText,
  permissionLine,
  rangesFromText,
  rangesText,
  saveBlocked,
  togglePart,
  type AccessValue,
} from "@/components/access/model";
import { describeChange, patchFor, type Meta } from "@/components/iiif/metadata-model";

describe("access", () => {
  it("names the levels, falling back to private", () => {
    expect(accessLabel("public")).toBe("Public");
    expect(accessLabel("restricted")).toBe("Restricted");
    expect(accessLabel(undefined)).toBe("Private");
    expect(accessLabel("signed-in")).toBe("Private");
  });

  it("lists the open parts in plain words", () => {
    expect(partsText(ALL_PARTS)).toBe("Media, text and index");
    expect(partsText(["index", "transcript"])).toBe("Text and index");
    expect(partsText(["media"])).toBe("Media");
    expect(partsText([])).toBe("Nothing");
    expect(partsText(null)).toBe("Nothing");
  });

  it("sums a recording's access up in one line", () => {
    expect(accessSummary({ access: "public", open: ALL_PARTS })).toBe("Public · everything open");
    expect(accessSummary({ access: "public", open: ["transcript", "index"], featured: true })).toBe(
      "Public · text and index open to everyone · featured",
    );
    expect(accessSummary({ access: "public", open: [] })).toBe("Public · page and description only");
    // parts and featured only mean something for a public recording
    expect(accessSummary({ access: "restricted", open: [], featured: true })).toBe("Restricted");
    expect(accessSummary(null)).toBe("Private");
  });

  it("toggles a part and keeps the canonical order", () => {
    expect(togglePart(["index"], "media", true)).toEqual(["media", "index"]);
    expect(togglePart(["media", "index"], "media", false)).toEqual(["index"]);
    expect(togglePart(["media"], "media", true)).toEqual(["media"]);
  });

  it("sends only what changed", () => {
    const saved: AccessValue = { access: "private", open: ALL_PARTS, featured: false };
    expect(accessPatch(saved, saved)).toEqual({});
    expect(accessPatch(saved, { ...saved, access: "public", featured: true })).toEqual({
      access: "public",
      featured: true,
    });
    // closing every part is a change of its own
    expect(accessPatch(saved, { ...saved, open: [] })).toEqual({ open: [] });
  });

  it("sends the downloads switch only when it changes, on by default", () => {
    const saved: AccessValue = { access: "private", open: ["media"], featured: false, downloads: true };
    expect(accessPatch(saved, saved)).toEqual({});
    expect(accessPatch(saved, { ...saved, downloads: false })).toEqual({ downloads: false });
    expect(accessPatch({ ...saved, downloads: false }, { ...saved, downloads: true })).toEqual({ downloads: true });
    // the metadata editor and the IIIF panel don't carry it: nothing to send
    const plain: AccessValue = { access: "private", open: ["media"], featured: false };
    expect(accessPatch(plain, plain)).toEqual({});
  });

  it("says why the files can't be saved, only when they can't", () => {
    expect(saveBlocked({ can_save: false })).toMatch(/turned downloads off: you can play and read it here/);
    expect(saveBlocked({ can_save: true })).toBeNull();
    expect(saveBlocked({})).toBeNull(); // an older server: as before
    expect(saveBlocked(undefined)).toBeNull();
  });

  it("keeps no open parts as a value when saving metadata, and describes it", () => {
    const draft: Meta = { access: "public", open: [], featured: false };
    expect(patchFor(draft, ["access", "open", "featured"])).toEqual({ access: "public", open: [], featured: false });
    expect(patchFor({ access: "public" }, ["open"])).toEqual({ open: null });
    expect(describeChange("open", ["media", "transcript"], [])).toBe("Open to everyone: Media and text → Nothing");
    expect(describeChange("open", undefined, ["index"])).toBe("Open to everyone: default → Index");
    expect(describeChange("open", ["index"], null)).toBe("Open to everyone cleared");
    expect(describeChange("featured", false, true)).toBe("Featured");
    expect(describeChange("featured", true, false)).toBe("No longer featured");
    expect(describeChange("access", "private", "restricted")).toBe("Access: private → restricted");
  });

  it("says who gave someone permission, and when", () => {
    expect(permissionLine({ by: "ana@example.org", at: "2026-09-30T19:40:00Z" })).toBe(
      "Given by ana@example.org on 30 Sept 2026",
    );
    expect(permissionLine({ by: null, at: "2026-09-30T19:40:00Z" })).toBe("Given on 30 Sept 2026");
    expect(permissionLine({ by: "ana@example.org", at: "not a date" })).toBe("Given by ana@example.org");
  });

  it("reads IP group addresses typed one per line, or separated by commas or spaces", () => {
    expect(rangesFromText(" 198.51.100.0/24\n\n2001:db8::/48, 198.51.100.7 ;198.51.100.0/24\t")).toEqual([
      "198.51.100.0/24",
      "2001:db8::/48",
      "198.51.100.7",
    ]);
    expect(rangesFromText("  \n ")).toEqual([]);
  });

  it("shows an IP group's ranges in one line", () => {
    expect(rangesText(["198.51.100.0/24"])).toBe("198.51.100.0/24");
    expect(rangesText(["a", "b", "c"])).toBe("a, b, c");
    expect(rangesText(["a", "b", "c", "d", "e"])).toBe("a, b and 3 more");
    expect(rangesText([])).toBe("");
  });

  it("says what an IP group opens", () => {
    expect(ipGroupOpens({ everything: true, chosen: 4 }, "pods")).toBe("Every asset in pods");
    expect(ipGroupOpens({ everything: false, chosen: 1 }, "pods")).toBe("1 chosen asset");
    expect(ipGroupOpens({ everything: false, chosen: 3 }, "pods")).toBe("3 chosen assets");
    expect(ipGroupOpens({ everything: false }, "pods")).toBe("No assets yet: choose them in an asset’s Access dialog");
  });
});
