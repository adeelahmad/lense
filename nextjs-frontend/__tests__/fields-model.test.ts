import type { FieldDef, FieldValue } from "@/app/openapi-client/types.gen";
import {
  changedValues,
  definedHere,
  definitionProblem,
  fieldFilterLabel,
  fromRaw,
  parseOptions,
  toDraft,
  toRaw,
  valueProblem,
  valueText,
  whereDefined,
} from "@/components/fields/fields-model";
import { NO_FILTERS } from "@/components/library/model";
import { describeView, fromView, sameState, viewState } from "@/components/library/views-model";

const def = (over: Partial<FieldDef>): FieldDef => ({
  id: 1,
  label: "Interviewer",
  type: "text",
  target: "resource",
  published: false,
  can_change: true,
  collection: null,
  collection_path: [],
  ...over,
});

describe("custom field values", () => {
  it("shows saved values in a form and back", () => {
    expect(toRaw(def({ type: "boolean" }), true)).toBe(true);
    expect(toRaw(def({ type: "boolean" }), null)).toBeNull();
    expect(toRaw(def({ type: "choices" }), ["a"])).toEqual(["a"]);
    expect(toRaw(def({ type: "number" }), 1998)).toBe("1998");
    expect(toRaw(def({}), null)).toBe("");
    expect(fromRaw(def({}), "  Ana   Ruiz ")).toBe("Ana Ruiz");
    expect(fromRaw(def({ type: "longtext" }), "  two\n\nlines ")).toBe("two\n\nlines");
    expect(fromRaw(def({ type: "number" }), " 1998.5 ")).toBe(1998.5);
    expect(fromRaw(def({ type: "choices", options: ["Work", "War"] }), ["War", "Work"])).toEqual(["Work", "War"]);
    expect(fromRaw(def({}), "   ")).toBeNull();
    expect(fromRaw(def({ type: "choices" }), [])).toBeNull();
  });

  it("says what the server would refuse", () => {
    expect(valueProblem(def({ type: "number", label: "Year" }), "about 1998")).toBe("Year is a number.");
    expect(valueProblem(def({ type: "number" }), "1998")).toBeNull();
    expect(valueProblem(def({ type: "date", label: "Recorded" }), "05/1998")).toBe(
      "Recorded is a date: YYYY, YYYY-MM or YYYY-MM-DD.",
    );
    expect(valueProblem(def({ type: "date", label: "Recorded" }), "1998-02-30")).toBe(
      "Recorded: 1998-02-30 isn't a day of the calendar.",
    );
    expect(valueProblem(def({ type: "date" }), "1998-05")).toBeNull();
    expect(valueProblem(def({ type: "link", label: "Aid" }), "javascript:x")).toBe(
      "Aid is a link starting with http:// or https://.",
    );
    expect(valueProblem(def({ type: "choice", label: "Format", options: ["Lecture"] }), "Panel")).toBe(
      "Format is one of: Lecture.",
    );
    expect(valueProblem(def({ type: "text" }), "x".repeat(501))).toBe("Interviewer can have up to 500 characters.");
    expect(valueProblem(def({ type: "number" }), "")).toBeNull(); // empty is no value
  });

  it("sends only what changed", () => {
    const values: FieldValue[] = [
      { field: def({ id: 1 }), value: "Ana" },
      { field: def({ id: 2, type: "number", label: "Year" }), value: 1998 },
      { field: def({ id: 3, type: "boolean", label: "Done" }), value: null },
    ];
    const draft = toDraft(values);
    expect(changedValues(values, draft)).toEqual({});
    expect(changedValues(values, { ...draft, 1: "", 2: "1998", 3: true })).toEqual({ "1": null, "3": true });
  });

  it("reads values and where fields are defined", () => {
    expect(valueText(def({ type: "boolean" }), false)).toBe("No");
    expect(valueText(def({ type: "choices" }), ["Work", "War"])).toBe("Work, War");
    expect(valueText(def({}), null)).toBe("—");
    expect(whereDefined(def({}), "pods")).toBe("pods");
    expect(whereDefined(def({ collection: 4, collection_path: ["Talks", "2024"] }), "pods")).toBe(
      "pods › Talks › 2024",
    );
    const fields = [def({ id: 1 }), def({ id: 2, collection: 4 })];
    expect(definedHere(fields, null).map((f) => f.id)).toEqual([1]);
    expect(definedHere(fields, 4).map((f) => f.id)).toEqual([2]);
  });

  it("checks a new field's definition", () => {
    expect(parseOptions("Lecture\n lecture \nPanel, Interview\n\n")).toEqual(["Lecture", "Panel", "Interview"]);
    expect(definitionProblem({ label: " ", type: "text", options: "" })).toBe("Name the field.");
    expect(definitionProblem({ label: "Format", type: "choice", options: " \n" })).toBe(
      "A choice field needs at least one option.",
    );
    expect(definitionProblem({ label: "Format", type: "choice", options: "Lecture" })).toBeNull();
  });

  it("keeps a field filter in saved views", () => {
    expect(fieldFilterLabel({ label: "Format", value: " Lecture " })).toBe("Format: Lecture");
    expect(fieldFilterLabel({ label: "Format", value: "" })).toBe("Format: any value");
    const state = {
      filters: { ...NO_FILTERS, field: { id: 7, value: "Lecture" } },
      view: "all" as const,
      sort: { key: "date" as const, dir: "desc" as const },
    };
    const saved = viewState(state);
    expect(saved).toMatchObject({ field: 7, value: "Lecture" });
    const back = fromView(saved);
    expect(back.filters.field).toEqual({ id: 7, value: "Lecture" });
    expect(sameState(viewState(back), saved)).toBe(true);
    expect(
      sameState(viewState({ ...back, filters: { ...back.filters, field: { id: 7, value: "Panel" } } }), saved),
    ).toBe(false);
    expect(describeView(saved, undefined, undefined, (id) => (id === 7 ? "Format" : null))).toBe(
      "All recordings · Format: Lecture",
    );
    expect(viewState({ ...state, filters: { ...NO_FILTERS } })).toMatchObject({ field: null, value: null });
  });
});
