import { audioFilename, MAX_MEDIA_BYTES, mediaProblem, textProblem } from "../model";

describe("raw voice input", () => {
  it("rejects empty text and oversized text, without truncating it", () => {
    expect(textProblem(" \n ")).toBeTruthy();
    expect(textProblem("x".repeat(4000))).toBeNull();
    expect(textProblem("x".repeat(4001))).toMatch(/not been shortened/);
    expect(textProblem("😀".repeat(4000))).toBeNull();
    expect(textProblem("😀".repeat(4001))).toBeTruthy();
  });
  it("enforces the raw media byte limit", () => {
    expect(mediaProblem(null)).toBeTruthy();
    expect(mediaProblem({ size: 0 })).toBeTruthy();
    expect(mediaProblem({ size: MAX_MEDIA_BYTES })).toBeNull();
    expect(mediaProblem({ size: MAX_MEDIA_BYTES + 1 })).toMatch(/25 MiB/);
  });
  it("uses the returned audio format, never assumes MP3", () => {
    expect(audioFilename("audio/wav")).toBe("lens-speech.wav");
    expect(audioFilename("audio/mpeg")).toBe("lens-speech.mp3");
    expect(audioFilename("audio/ogg; codecs=opus")).toBe("lens-speech.ogg");
    expect(audioFilename("audio/x-aiff")).toBe("lens-speech.aiff");
    expect(audioFilename("application/octet-stream")).toBe("lens-speech.audio");
  });
});
