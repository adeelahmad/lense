import {
  nextPiece,
  pieceCount,
  resumeFrom,
  retryDelay,
  retryable,
  sentShare,
  sentText,
  storedName,
  uploadError,
} from "@/components/import/upload-model";

const MB = 1024 * 1024;

describe("uploading in pieces", () => {
  it("sends the next piece from where the server got to", () => {
    expect(nextPiece(0, 20 * MB, 8)).toEqual([0, 8 * MB]);
    expect(nextPiece(16 * MB, 20 * MB, 8)).toEqual([16 * MB, 20 * MB]);
    expect(nextPiece(5, 10, 0)).toEqual([5, 10]);
    expect(pieceCount(20 * MB, 8)).toBe(3);
    expect(pieceCount(10, 8)).toBe(1);
  });

  it("tries a piece again after a dropped connection or a server error, waiting longer each time", () => {
    expect([0, 408, 429, 500, 502, 503].every(retryable)).toBe(true);
    expect([400, 403, 404, 413, 507].some(retryable)).toBe(false);
    expect([1, 2, 3, 4, 5, 6, 9].map(retryDelay)).toEqual([1000, 2000, 4000, 8000, 16000, 30000, 30000]);
  });

  it("carries on an unfinished upload of the same file into the same namespace", () => {
    const up = (over: object) => ({
      state: "receiving" as const,
      namespace: "pods",
      size: 100,
      filename: "ep 1.m4a",
      ...over,
    });
    const uploads = [up({ namespace: "calls" }), up({ size: 99 }), up({ state: "done" }), up({ id: "x" })];
    expect(resumeFrom(uploads, { name: "ep 1.M4A", size: 100 }, "pods")).toEqual(up({ id: "x" }));
    expect(resumeFrom(uploads, { name: "ep 2.m4a", size: 100 }, "pods")).toBeNull();
  });

  it("knows the name the server keeps a file under", () => {
    expect(storedName("C:\\Users\\me\\..\\.hidden\u0000 talk .MP3")).toBe("hidden talk.mp3");
    expect(storedName("a/b/Interview\u00a0one.WAV")).toBe("Interviewone.wav");
    expect(storedName("...")).toBe("upload");
    expect(storedName("notes")).toBe("notes");
  });

  it("says how far it got and what went wrong", () => {
    expect(sentText(36 * MB, 80 * MB)).toBe("36.0 MB of 80.0 MB");
    expect(sentShare(50, 200)).toBe(0.25);
    expect(sentShare(5, 0)).toBe(0);
    expect(sentShare(300, 200)).toBe(1);
    expect(uploadError(404, "not found")).toMatch(/cancelled or has expired/);
    expect(uploadError(507, "x")).toMatch(/no room/);
    expect(uploadError(0, "x")).toMatch(/Can’t reach the server/);
    expect(uploadError(400, "TXT files can't be uploaded")).toBe("TXT files can't be uploaded");
    expect(uploadError(413, "files up to 1 MB")).toBe("Files up to 1 MB");
  });
});
