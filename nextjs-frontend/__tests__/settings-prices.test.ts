import { linesToPrices, pricesToLines } from "@/components/settings/model";

describe("model prices", () => {
  it("round-trips every unit", () => {
    const text = [
      "gpt-4o-mini 0.15 0.6",
      "gpu-box 0.6 per hour",
      "deepgram:nova-3 0.0043 per minute",
      "openai:tts-1 15 per million characters",
      "local off",
    ].join("\n");
    const parsed = linesToPrices(text);
    expect(parsed).toEqual({
      value: {
        "gpt-4o-mini": { input: 0.15, output: 0.6 },
        "gpu-box": { unit: "time", per_hour: 0.6 },
        "deepgram:nova-3": { unit: "minute", per_minute: 0.0043 },
        "openai:tts-1": { unit: "characters", per_million: 15 },
        local: { unit: "off" },
      },
    });
    expect(pricesToLines((parsed as { value: unknown }).value)).toBe(text);
  });

  it("names the line that's wrong", () => {
    expect(linesToPrices("gpt 1 2\nwhisper-1 cheap per minute")).toEqual({ error: expect.stringContaining("Line 2") });
    expect("error" in linesToPrices("gpt-4o-mini 0.15")).toBe(true);
  });
});
