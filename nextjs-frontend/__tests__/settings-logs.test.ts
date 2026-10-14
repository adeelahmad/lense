import { levelsToLines, linesToLevels } from "@/components/settings/model";

describe("logger levels", () => {
  it("round-trips lines of logger and level", () => {
    const parsed = linesToLevels("httpx warning\n\n lens.bridge DEBUG ");
    expect(parsed).toEqual({ value: { httpx: "warning", "lens.bridge": "debug" } });
    expect(levelsToLines({ httpx: "warning" })).toBe("httpx warning");
  });

  it("names the line that's wrong", () => {
    expect(linesToLevels("httpx warning\nhttpx loud")).toEqual({
      error: expect.stringContaining("Line 2"),
    });
    expect("error" in linesToLevels("bad name! info")).toBe(true);
  });
});
