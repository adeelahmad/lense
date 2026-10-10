import { hostNames, inProgress, sourceHref, stageIndex, statusLabel } from "@/components/podcasts/model";

describe("podcasts", () => {
  it("knows when an episode is still being made", () => {
    expect(inProgress("queued")).toBe(true);
    expect(inProgress("writing")).toBe(true);
    expect(inProgress("ready")).toBe(false);
    expect(inProgress("script_only")).toBe(false);
    expect(inProgress("failed")).toBe(false);
  });

  it("places the status among the stages", () => {
    expect(stageIndex("queued")).toBe(-1);
    expect(stageIndex("gathering")).toBe(0);
    expect(stageIndex("checking")).toBe(3);
    expect(stageIndex("ready")).toBe(6);
    expect(statusLabel("rendering")).toBe("Recording the voices");
    expect(statusLabel("script_only")).toBe("Script only");
  });

  it("links a citation to its source's moment or page", () => {
    expect(sourceHref({ recording: 4, t0: 61_500 })).toBe("/resources/4?t=61");
    expect(sourceHref({ recording: 4, t0: 0 })).toBe("/resources/4");
    expect(sourceHref({ recording: 4, t0: 9000, page: 3 })).toBe("/resources/4?page=3");
    expect(sourceHref({ recording: null })).toBeNull();
  });

  it("falls back to the default hosts", () => {
    expect(hostNames(undefined)).toEqual({ a: "Alex", b: "Sam" });
    expect(hostNames({ hosts: { a: "Ada", b: "" } })).toEqual({ a: "Ada", b: "Sam" });
  });
});
