import { isActive } from "@/components/app-shell/nav-config";
import { cleanSpec, moveStep, orderProblem, sameSteps, specProblems, stepSummary, whenText } from "@/components/pipelines/pipeline-model";
import {
  checkAgainstSchema,
  checkTemplate,
  completionContext,
  completions,
  declaredNames,
  lineDiff,
  parseSchema,
  parseUnifiedDiff,
  readNames,
  schemaSummary,
  trimContext,
} from "@/components/templates/template-model";

const std = ["transcribe", "diarize", "analyze", "summarize", "report"].map((type) => ({ type }));

describe("pipeline order", () => {
  it("moves a step when nothing it needs ends up after it", () => {
    const r = moveStep([{ type: "transcribe" }, { type: "analyze" }, { type: "shots" }], 2, 0);
    expect(r.error).toBeUndefined();
    expect(r.steps.map((s) => s.type)).toEqual(["shots", "transcribe", "analyze"]);
    expect(moveStep(std, 4, 3).error).toBe("Report needs Summarize’s output, so it has to come after it.");
  });
  it("snaps back and says which step it needs", () => {
    const r = moveStep(std, 1, 0);
    expect(r.steps).toBe(std);
    expect(r.error).toBe("Diarize needs Transcribe’s output, so it has to come after it.");
    expect(moveStep(std, 0, 4).error).toMatch(/needs Transcribe/);
  });
  it("only minds steps that are in the pipeline", () => {
    expect(orderProblem([{ type: "analyze" }, { type: "summarize" }, { type: "report" }])).toBeNull();
    expect(orderProblem([{ type: "summarize" }, { type: "analyze" }])).toMatch(/Summarize needs Analyze/);
  });
});

describe("navigation", () => {
  it("keeps Pipelines active on its Templates tab", () => {
    expect(isActive("/templates", "/pipelines")).toBe(true);
    expect(isActive("/templates/3", "/pipelines")).toBe(true);
    expect(isActive("/templatesx", "/pipelines")).toBe(false);
  });
});

describe("pipeline specs", () => {
  it("keeps saved specs minimal", () => {
    expect(cleanSpec({ type: "analyze", name: " ", when: { min_minutes: undefined } })).toBe("analyze");
    expect(cleanSpec({ type: "llm", template: 1, key: "notes", when: { min_minutes: 5 } })).toEqual({ type: "llm", template: 1, key: "notes", when: { min_minutes: 5 } });
    expect(sameSteps(["analyze"], [{ type: "analyze" }])).toBe(true);
  });
  it("describes settings and conditions", () => {
    expect(whenText({ min_minutes: 5 })).toBe("only if > 5 min");
    expect(whenText({ min_minutes: 5, max_minutes: 60, source: "audio", languages: ["en"] })).toBe("only if 5–60 min · only audio · en");
    expect(stepSummary({ type: "llm", template: 1, version: 3, key: "notes" }, () => "Meeting notes")).toBe("Meeting notes v3 · outputs.notes");
    expect(stepSummary({ type: "report" })).toBe("built-in report");
  });
  it("finds what the backend would reject", () => {
    const kinds = (id: number) => ({ 1: "prompt", 2: "export" })[id];
    expect(specProblems([{ type: "llm" }], kinds)).toEqual({ 0: "Choose a prompt template." });
    expect(specProblems([{ type: "llm", template: 2, key: "x" }], kinds)).toEqual({ 0: "Needs a prompt template." });
    expect(specProblems([{ type: "llm", template: 1, key: "Bad Key" }], kinds)[0]).toMatch(/lowercase/);
    expect(specProblems([{ type: "export", template: 2 }], kinds)[0]).toMatch(/file name/);
    expect(specProblems([], kinds)[-1]).toMatch(/at least one step/);
  });
});

describe("template variables", () => {
  it("reads only the names an expression uses", () => {
    expect(readNames(" speaker.names | join(', ') ").map((n) => n.name)).toEqual(["speaker"]);
    expect(readNames(" x is defined and y(a=1, 'str') ").map((n) => n.name)).toEqual(["x", "y"]);
  });
  it("knows names the template defines", () => {
    expect(declaredNames("{% for s in speakers %}{% set notes = outputs.get('x', {}) %}{% endfor %}").sort()).toEqual(["notes", "s"]);
  });
  it("flags unknown variables with the line and a suggestion", () => {
    const body = "Summary of {{ recording.title }}\n{% for s in speakers %}{{ s.name }}{% endfor %}\nOwner: {{ speaker.names }}";
    const p = checkTemplate(body);
    expect(p).toHaveLength(1);
    expect(p[0]).toMatchObject({ line: 3, name: "speaker", suggestion: "speakers" });
    expect(p[0].message).toBe("Line 3: {{ speaker.names }} isn’t a variable. Did you mean {{ speakers }}?");
    expect(body.slice(p[0].index, p[0].index + p[0].length)).toBe("speaker");
  });
  it("accepts the seeded templates", () => {
    const seeded =
      "{% set notes = outputs.get('meeting_notes', {}) %}{% if notes %}<p>{{ notes.tldr }}</p>{% for a in notes.action_items %}{{ a.task }}{% endfor %}{% endif %}" +
      "{% for s in speakers %}{{ s.name }} {{ (s.talk_ms or 0) | tc }}{% if not loop.last %}, {% endif %}{% endfor %}{{ keywords[:15] | join(\", \") }}";
    expect(checkTemplate(seeded)).toEqual([]);
  });
  it("completes after {{", () => {
    expect(completionContext("Hello {{ rec", 12)).toEqual({ from: 9, prefix: "rec" });
    expect(completionContext("Hello {{ x }} rec", 17)).toBeNull();
    expect(completionContext("{{ x | jo", 9)).toBeNull();
    expect(completions("sp").map((c) => c.key)).toEqual(["speakers", "speakers[0].name"]);
  });
});

describe("output schema", () => {
  const schema = { type: "object", required: ["tldr", "key_points"], properties: { tldr: { type: "string" }, key_points: { type: "array", items: { type: "string" }, minItems: 3, maxItems: 6 } } };
  it("parses and explains mistakes", () => {
    expect(parseSchema('{"type":"object"}').ok).toBe(true);
    expect(parseSchema('{"type":"array"}')).toMatchObject({ ok: false, error: expect.stringContaining("type") });
    const bad = parseSchema('{\n  "type": "object",\n  "properties": {,}\n}');
    expect(bad.ok).toBe(false);
  });
  it("checks a result against it", () => {
    expect(checkAgainstSchema({ tldr: "x", key_points: ["a", "b", "c"] }, schema)).toEqual([]);
    expect(checkAgainstSchema({ key_points: ["a"] }, schema)).toEqual(["tldr is missing", "key_points has 1 items; at least 3"]);
    expect(checkAgainstSchema({ tldr: 1, key_points: ["a", "b", 3] }, schema)).toEqual(["tldr should be string, not integer", "key_points[2] should be string, not integer"]);
  });
  it("summarises it", () => {
    expect(schemaSummary(schema)).toBe("tldr (string), key_points (3–6)");
  });
});

describe("versions", () => {
  it("parses a unified diff with line numbers", () => {
    const text = "--- version 2\n+++ version 3\n@@ -12,3 +12,4 @@\n Return JSON matching the schema.\n-List any action items.\n+Attribute each action item to the\n+speaker who owns it.\n Keep key_points to 3–6 items.\n";
    const rows = parseUnifiedDiff(text);
    expect(rows[0]).toEqual({ kind: "hunk", text: "@@ -12,3 +12,4 @@" });
    expect(rows.slice(1)).toEqual([
      { kind: "line", sign: " ", a: 12, b: 12, text: "Return JSON matching the schema." },
      { kind: "line", sign: "-", a: 13, text: "List any action items." },
      { kind: "line", sign: "+", b: 13, text: "Attribute each action item to the" },
      { kind: "line", sign: "+", b: 14, text: "speaker who owns it." },
      { kind: "line", sign: " ", a: 14, b: 15, text: "Keep key_points to 3–6 items." },
    ]);
  });
  it("diffs the unsaved draft line by line", () => {
    const rows = lineDiff("a\nb\nc", "a\nB\nc\nd");
    expect(rows.map((r) => (r.kind === "line" ? `${r.sign}${r.text}` : ""))).toEqual([" a", "+B", "-b", " c", "+d"]);
    expect(trimContext(lineDiff("same", "same"))).toEqual([]);
  });
});
