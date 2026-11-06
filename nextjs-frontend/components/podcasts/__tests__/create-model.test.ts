import { MODES, selectedVoices } from "../create-model";

describe("podcast output choices", () => {
  it("offers only the four supported modes, with dialogue first", () => {
    expect(MODES.map((m) => m.value)).toEqual(["dialogue", "original", "summary", "explanation"]);
  });
  it("keeps podcast defaults when neither voice is chosen", () => {
    expect(selectedVoices("dialogue", "", "")).toBeNull();
    expect(selectedVoices("dialogue", "", "nova")).toEqual({ a: null, b: "nova" });
  });
  it("sends both actual provider IDs for dialogue", () => {
    expect(selectedVoices("dialogue", "voice-123", "voice-456")).toEqual({ a: "voice-123", b: "voice-456" });
  });
  it.each(["original", "summary", "explanation"] as const)("never sends voice B for %s", (mode) => {
    expect(selectedVoices(mode, "voice-123", "voice-456")).toEqual({ a: "voice-123", b: null });
    expect(selectedVoices(mode, "", "voice-456")).toBeNull();
  });
});
