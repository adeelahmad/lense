import { suggestedTokenName } from "@/components/account/token-model";
import { suggestedPipelineName } from "@/components/pipelines/pipeline-model";
import { suggestedName } from "@/components/routines/routine-model";
import { suggestedTemplateName } from "@/components/templates/template-model";
import { suggestedWorkflowName } from "@/components/workflows/workflow-model";
import { nameFromEmail, nsSlug, uniqueName } from "@/lib/format";

describe("less typing", () => {
  it("turns a typed namespace name into a valid one", () => {
    expect(nsSlug("Customer Calls")).toBe("customer-calls");
    expect(nsSlug("  Équipe_2026!")).toBe("equipe_2026");
    expect(nsSlug("-x")).toBe("x");
  });

  it("names a person from their email", () => {
    expect(nameFromEmail("ana.lopez@x.io")).toBe("Ana Lopez");
    expect(nameFromEmail("bob")).toBe("Bob");
    expect(nameFromEmail("")).toBe("");
  });

  it("names a routine nobody named", () => {
    expect(suggestedName("daily", [{ type: "sync" }, { type: "pipeline" }])).toBe(
      "Nightly: sync sources, run a pipeline",
    );
    expect(suggestedName("manual", [])).toBe("On demand routine");
  });

  it("guesses a name nothing has yet", () => {
    expect(uniqueName("Report page", [])).toBe("Report page");
    expect(uniqueName("Report page", ["report page ", "Report page 2", null])).toBe("Report page 3");
  });

  it("names a pipeline, workflow, template and token nobody named", () => {
    const standard = ["transcribe", "diarize", "analyze", "embed", "summarize", "report"].map((type) => ({ type }));
    expect(suggestedPipelineName(standard, [])).toBe("Transcribe to report");
    expect(suggestedPipelineName([{ type: "analyze" }, { type: "summarize" }], [])).toBe("Analyze and summarize");
    expect(suggestedPipelineName([{ type: "llm", name: "Meeting notes" }], ["Meeting notes"])).toBe("Meeting notes 2");
    expect(suggestedPipelineName([], [])).toBe("New pipeline");
    expect(suggestedWorkflowName("recording", ["Asset workflow"])).toBe("Asset workflow 2");
    expect(suggestedWorkflowName("graph", [])).toBe("Graph workflow");
    expect(suggestedTemplateName("prompt", ["Meeting notes"])).toBe("Prompt template");
    expect(suggestedTemplateName("export", ["Export file"])).toBe("Export file 2");
    expect(suggestedTokenName("read", [])).toBe("Read only token");
    expect(suggestedTokenName("write", ["Read & write token"])).toBe("Read & write token 2");
  });
});

describe("a file's language", () => {
  it("is read from its name", () => {
    const { guessLanguage } = jest.requireActual("@/components/recording/files-model");
    expect(guessLanguage("talk.pt-BR.vtt")).toBe("pt-BR");
    expect(guessLanguage("talk.pt_br.vtt")).toBe("pt-BR");
    expect(guessLanguage("notes.en.srt")).toBe("en");
    expect(guessLanguage("es-419.x.es-419.vtt")).toBe("es-419");
    expect(guessLanguage("report.v2.pdf")).toBe("");
    expect(guessLanguage("harbour.vtt")).toBe("");
  });
});
