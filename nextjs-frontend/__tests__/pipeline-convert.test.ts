import { cleanSpec, moveStep, newStep, specProblems, stepSummary } from "@/components/pipelines/pipeline-model";
import { stepLabel } from "@/components/recording/jobs";

describe("the convert step in a pipeline", () => {
  it("is added from the library ready to run: to audio unless images are picked", () => {
    expect(newStep("convert")).toEqual({ type: "convert", to: "audio" });
    expect(newStep("convert:images")).toEqual({ type: "convert", to: "images" });
    expect(newStep("llm")).toEqual({ type: "llm", key: "" });
    expect(newStep("workflow:3")).toEqual({ type: "workflow", workflow: 3 });
    expect(stepLabel("convert")).toBe("Convert");
  });
  it("keeps only the settings it has, the format only for audio", () => {
    expect(cleanSpec({ type: "convert", to: "audio", format: "mp3", pipeline: "none" })).toEqual({
      type: "convert",
      to: "audio",
      format: "mp3",
      pipeline: "none",
    });
    expect(cleanSpec({ type: "convert", to: "images", format: "mp3", pipeline: 4 })).toEqual({
      type: "convert",
      to: "images",
      pipeline: 4,
    });
    expect(cleanSpec({ type: "analyze", to: "audio" })).toBe("analyze");
  });
  it("says what it makes, and asks for what to convert to", () => {
    expect(stepSummary({ type: "convert", to: "audio" })).toBe("to audio · m4a");
    expect(stepSummary({ type: "convert", to: "images", pipeline: "none" })).toBe("pages to images · not processed");
    expect(specProblems([{ type: "convert" }], () => undefined)).toEqual({
      0: "Choose what to convert to: audio or images.",
    });
    expect(specProblems([newStep("convert")], () => undefined)).toEqual({});
  });
  it("comes after the pages are read", () => {
    const r = moveStep([{ type: "read" }, { type: "convert", to: "images" }], 1, 0);
    expect(r.error).toBe("Convert needs Read pages’s output, so it has to come after it.");
  });
});
