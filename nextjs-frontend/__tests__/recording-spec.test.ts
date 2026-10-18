import { moveStep, specProblems, stepSummary } from "@/components/pipelines/pipeline-model";
import { stepLabel } from "@/components/recording/jobs";
import { specAction } from "@/components/recording/spec-model";

describe("the More menu's spec items", () => {
  it("offers to write a spec until there is one", () => {
    expect(specAction(undefined, true)).toEqual({ open: undefined, write: "Write a spec", disabled: false });
    expect(specAction({ status: "none" }, true)).toEqual({ open: undefined, write: "Write a spec", disabled: false });
    expect(specAction({ status: "failed" }, true).write).toBe("Write a spec");
  });
  it("opens the spec and writes it again once it's written", () => {
    expect(specAction({ status: "ready", note: 41 }, true)).toEqual({
      open: "/notes/41",
      write: "Write the spec again",
      disabled: false,
    });
  });
  it("waits while it's being written, and for the right to process the asset", () => {
    expect(specAction({ status: "writing", note: 41 }, true)).toEqual({
      open: "/notes/41",
      write: "Writing the spec…",
      disabled: true,
    });
    expect(specAction({ status: "none" }, false).disabled).toBe(true);
  });
});

describe("the spec step in a pipeline", () => {
  it("goes by its name and comes after the transcript", () => {
    expect(stepLabel("spec")).toBe("Spec");
    const r = moveStep([{ type: "transcribe" }, { type: "spec" }], 1, 0);
    expect(r.error).toBe("Spec needs Transcribe’s output, so it has to come after it.");
  });
  it("uses Spec from transcript unless it names another prompt template", () => {
    expect(stepSummary({ type: "spec" })).toBe("Spec from transcript");
    expect(specProblems([{ type: "spec" }], () => undefined)).toEqual({});
    expect(specProblems([{ type: "spec", template: 3 }], () => "report")).toEqual({ 0: "Needs a prompt template." });
    expect(specProblems([{ type: "spec", template: 4 }], () => "prompt")).toEqual({});
  });
});
