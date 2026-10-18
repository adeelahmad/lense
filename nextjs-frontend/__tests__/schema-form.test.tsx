import { fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";
import { useState } from "react";

import { SchemaForm, type Variable } from "@/components/forms/schema-form";
import {
  fieldProblem,
  fieldsOf,
  fromText,
  insertVariable,
  openVariable,
  toText,
  type JsonSchema,
} from "@/components/forms/schema-model";

// the shapes of the actions catalog's params (GET /api/v1/automations/actions)
const notify: JsonSchema = {
  type: "object",
  properties: {
    to: {
      type: "string",
      title: "To",
      default: "me",
      maxLength: 200,
      description: "me, target:<id> or gateway:<name>",
    },
    text: { type: "string", title: "Message", maxLength: 4000 },
    link: { type: "string", title: "Link", maxLength: 2000 },
  },
};
const mixed: JsonSchema = {
  type: "object",
  properties: {
    method: { type: "string", title: "Method", enum: ["GET", "POST", "PUT"], default: "GET" },
    collection: { type: "integer", title: "Collection" },
    propose_only: { type: "boolean", title: "Only propose its changes", default: false },
    tools: { type: "array", title: "Tools", items: { type: "string" } },
    headers: { type: "object", title: "Headers", default: {} },
    asset: { title: "Asset", default: "{{ event.object.id }}" },
    prompt: { type: "string", title: "Prompt", format: "prompt" },
  },
};

describe("schema fields", () => {
  it("gives each property the control its type needs", () => {
    const fs = fieldsOf(mixed, ["collection"]);
    expect(fs.map((f) => [f.key, f.kind, f.required])).toEqual([
      ["method", "choice", false],
      ["collection", "number", true],
      ["propose_only", "toggle", false],
      ["tools", "list", false],
      ["headers", "json", false],
      ["asset", "any", false],
      ["prompt", "long", false],
    ]);
    expect(fieldsOf(notify).map((f) => f.kind)).toEqual(["text", "long", "long"]);
    expect(fs[0]).toMatchObject({ label: "Method", options: ["GET", "POST", "PUT"], default: "GET", template: false });
  });

  it("reads what's typed as the value, templates standing for any type", () => {
    const [, collection, , tools, headers, asset] = fieldsOf(mixed);
    expect(fromText(collection, "12")).toBe(12);
    expect(fromText(collection, "{{ vars.box }}")).toBe("{{ vars.box }}");
    expect(fromText(collection, "")).toBeUndefined();
    expect(fromText(tools, "search\n\nweb_fetch ")).toEqual(["search", "web_fetch"]);
    expect(fromText(headers, '{"X-Key": "a"}')).toEqual({ "X-Key": "a" });
    expect(fromText(asset, "7")).toBe(7);
    expect(fromText(asset, "lens:recording/7")).toBe("lens:recording/7");
    expect(toText(tools, ["a", "b"])).toBe("a\nb");
    expect(toText(headers, { a: 1 })).toBe('{\n  "a": 1\n}');
  });

  it("says what's wrong with a value", () => {
    const [method, collection, , , headers] = fieldsOf(mixed, ["collection"]);
    expect(fieldProblem(collection, undefined)).toBe("Fill in Collection.");
    expect(fieldProblem(collection, "abc")).toBe("A number, or a {{ }} template.");
    expect(fieldProblem(collection, "{{ vars.box }}")).toBe("");
    expect(fieldProblem(headers, "{bad")).toMatch(/object in JSON/);
    expect(fieldProblem(method, "DELETE")).toBe("One of GET, POST, PUT.");
    expect(fieldProblem(method, undefined)).toBe("");
  });

  it("finds the {{ being typed and puts a variable in its place", () => {
    expect(openVariable("Hi {{ eve", 9)).toEqual({ start: 3, query: "eve" });
    expect(openVariable("Hi {{ event }} there", 20)).toBeNull();
    expect(insertVariable("Hi {{ eve", 9, "event.summary")).toEqual({ text: "Hi {{ event.summary }}", caret: 22 });
    expect(insertVariable("Hi {{ eve }}!", 9, "event.summary").text).toBe("Hi {{ event.summary }}!");
  });
});

const vars: Variable[] = [
  { path: "event.summary", label: "What happened" },
  { path: "event.actor.name", label: "Who" },
  { path: "now", label: "The time" },
];

function Harness({ schema, required, spy }: { schema: JsonSchema; required?: string[]; spy: jest.Mock }) {
  const [v, setV] = useState<Record<string, unknown>>({});
  return (
    <SchemaForm
      schema={schema}
      required={required}
      value={v}
      onChange={(x) => {
        spy(x);
        setV(x);
      }}
      variables={vars}
      preview={(k, x) => `preview of ${k}: ${String(x)}`}
    />
  );
}

describe("SchemaForm", () => {
  it("labels fields from the schema and fills the value in as you type", () => {
    const spy = jest.fn();
    render(<Harness schema={notify} required={["text"]} spy={spy} />);
    expect(screen.getByLabelText(/^To/)).toHaveAttribute("placeholder", "me");
    expect(screen.getByText("me, target:<id> or gateway:<name>")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/^Message/), { target: { value: "School wrote" } });
    expect(spy).toHaveBeenLastCalledWith({ text: "School wrote" });
    expect(screen.getByText("preview of text: School wrote")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/^Message/), { target: { value: "" } });
    expect(spy).toHaveBeenLastCalledWith({});
  });

  it("sets numbers, choices, toggles and lists as their types", () => {
    const spy = jest.fn();
    render(<Harness schema={mixed} required={["collection"]} spy={spy} />);
    fireEvent.change(screen.getByLabelText(/^Collection/), { target: { value: "4" } });
    fireEvent.change(screen.getByLabelText(/^Method/), { target: { value: "POST" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "Only propose its changes" }));
    fireEvent.change(screen.getByLabelText(/Tools/), { target: { value: "search\nweb" } });
    expect(spy).toHaveBeenLastCalledWith({
      collection: 4,
      method: "POST",
      propose_only: true,
      tools: ["search", "web"],
    });
    fireEvent.change(screen.getByLabelText(/^Collection/), { target: { value: "four" } });
    fireEvent.blur(screen.getByLabelText(/^Collection/));
    expect(screen.getByRole("alert")).toHaveTextContent("A number, or a {{ }} template.");
  });

  it("opens the variable picker on {{ and puts the pick in with Enter", () => {
    const spy = jest.fn();
    render(<Harness schema={notify} spy={spy} />);
    const box = screen.getByLabelText(/^Message/) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "From {{ ev", selectionStart: 10 } });
    const list = screen.getByRole("listbox", { name: "Variables" });
    expect(list.querySelectorAll("[role=option]")).toHaveLength(2);
    fireEvent.keyDown(box, { key: "ArrowDown" });
    expect(screen.getAllByRole("option")[1]).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(box, { key: "Enter" });
    expect(spy).toHaveBeenLastCalledWith({ text: "From {{ event.actor.name }}" });
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("closes the picker with Esc, and a click picks too", () => {
    const spy = jest.fn();
    render(<Harness schema={notify} spy={spy} />);
    const box = screen.getByLabelText(/^Message/);
    fireEvent.change(box, { target: { value: "{{", selectionStart: 2 } });
    expect(screen.getAllByRole("option")).toHaveLength(3);
    fireEvent.keyDown(box, { key: "Escape" });
    expect(screen.queryByRole("listbox")).toBeNull();
    fireEvent.change(box, { target: { value: "At {{ no", selectionStart: 8 } });
    fireEvent.mouseDown(screen.getByRole("option", { name: /The time/ }));
    expect(spy).toHaveBeenLastCalledWith({ text: "At {{ now }}" });
  });

  it("shows the API's problems next to their fields, and says when there's nothing to set", () => {
    const { rerender } = render(
      <SchemaForm schema={notify} value={{}} onChange={() => {}} errors={{ text: "notify needs Message (text)" }} />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("notify needs Message (text)");
    rerender(<SchemaForm schema={{ type: "object", properties: {} }} value={{}} onChange={() => {}} />);
    expect(screen.getByText("Nothing to set.")).toBeInTheDocument();
  });
});
