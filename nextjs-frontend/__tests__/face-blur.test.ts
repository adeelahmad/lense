import type { FaceBlur } from "@/app/openapi-client/types.gen";
import { blurRequest, blurring, blurStatus, copyLine } from "@/components/recording/video/face-blur-model";

const job = (over: Partial<NonNullable<FaceBlur["job"]>>): FaceBlur => ({ job: { id: 7, status: "running", ...over } });

describe("blurring faces for sharing", () => {
  it("asks for every face when all are ticked, else for the ones ticked in order", () => {
    expect(blurRequest(new Set(["a", "b"]), ["a", "b"])).toEqual({ who: "all" });
    expect(blurRequest(new Set(["c", "a"]), ["a", "b", "c"])).toEqual({ who: "chosen", tracks: ["a", "c"] });
  });

  it("keeps asking while the job is still to finish", () => {
    expect(blurring(job({ status: "queued" }))).toBe(true);
    expect(blurring(job({ status: "held" }))).toBe(true);
    expect(blurring(job({ status: "succeeded" }))).toBe(false);
    expect(blurring({ blurred: null, job: null })).toBe(false);
    expect(blurring(undefined)).toBe(false);
  });

  it("says how far it got, why it waits and why it failed", () => {
    expect(blurStatus(job({ progress: 40 }))).toEqual({ text: "Blurring faces… 40%", tone: "intent", progress: 0.4 });
    expect(blurStatus(job({}))).toEqual({ text: "Blurring faces…", tone: "intent", progress: null });
    expect(blurStatus(job({ status: "queued", waits: "heavy steps run 01:00-06:00 (UTC)" }))?.text).toBe(
      "Waiting to start: heavy steps run 01:00-06:00 (UTC)",
    );
    expect(blurStatus(job({ status: "failed", error: "couldn't blur the faces: no ffmpeg" }))).toEqual({
      text: "Failed: couldn't blur the faces: no ffmpeg",
      tone: "red",
      progress: null,
    });
    expect(blurStatus(job({ status: "failed" }))?.text).toBe("Couldn’t blur the faces");
    expect(blurStatus(job({ status: "succeeded" }))).toBeNull();
    expect(blurStatus(null)).toBeNull();
  });

  it("describes the copy", () => {
    const now = Date.parse("2026-10-19T12:10:00Z");
    const copy = {
      file: 3,
      name: "review-faces-blurred.mp4",
      download: "/x",
      faces: 2,
      who: "all",
      size: 1536,
      made_at: "2026-10-19T12:00:00Z",
    };
    expect(copyLine(copy, now)).toBe("2 faces blurred · 1.5 KB · 10 minutes ago");
    expect(copyLine({ ...copy, faces: 1, size: null, made_at: null }, now)).toBe("1 face blurred");
  });
});
