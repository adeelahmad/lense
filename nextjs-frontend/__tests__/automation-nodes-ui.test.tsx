import { fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";
import { useState } from "react";

import { ActionSettings, DecideSettings } from "@/components/workflows/action-settings";
import type { ActionDef } from "@/components/workflows/workflow-model";

const actions: ActionDef[] = [
  {
    name: "notify",
    label: "Send a message",
    group: "Messages",
    description: "Tells you in your own chat app",
    params: {
      type: "object",
      properties: { to: { type: "string", title: "To", default: "me" }, text: { type: "string", title: "Message" } },
    },
    required: ["text"],
    effect: "depends",
  },
  {
    name: "http_request",
    label: "Call a web address",
    group: "Web",
    params: { type: "object", properties: { url: { type: "string", title: "Address" } } },
    required: ["url"],
    effect: "out",
  },
];

function Harness({
  which,
  spy,
  start,
}: {
  which: "action" | "decide";
  spy: jest.Mock;
  start: Record<string, unknown>;
}) {
  const [c, setC] = useState(start);
  const set = (patch: Record<string, unknown>) => {
    const next = { ...c, ...patch };
    for (const k of Object.keys(next)) if (next[k] === undefined) delete next[k];
    spy(next);
    setC(next);
  };
  return which === "action" ? (
    <ActionSettings config={c} set={set} actions={actions} nodeId="n1" variables={[{ path: "event.summary" }]} />
  ) : (
    <DecideSettings config={c} set={set} />
  );
}

describe("an automation's nodes on the canvas", () => {
  it("sets an action up from the catalog, its params drawn from its schema", () => {
    const spy = jest.fn();
    render(<Harness which="action" spy={spy} start={{ action: "notify", params: {} }} />);
    expect(screen.getByText("Tells you in your own chat app")).toBeInTheDocument();
    expect(screen.getByText("Asks you first when it reaches other people")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/^Message/), { target: { value: "New: {{ event.summary }}" } });
    expect(spy).toHaveBeenLastCalledWith({ action: "notify", params: { text: "New: {{ event.summary }}" } });
    fireEvent.change(screen.getByLabelText(/^Wait first/), { target: { value: "30m" } });
    expect(spy).toHaveBeenLastCalledWith(expect.objectContaining({ delay: "30m" }));
  });

  it("clears the params when the action changes, and says an outside action asks first", () => {
    const spy = jest.fn();
    render(<Harness which="action" spy={spy} start={{ action: "notify", params: { text: "hi" } }} />);
    fireEvent.change(screen.getByLabelText(/^Action/), { target: { value: "http_request" } });
    expect(spy).toHaveBeenLastCalledWith({ action: "http_request", params: {} });
    expect(screen.getByText("Asks you first")).toBeInTheDocument();
    expect(screen.getByLabelText(/^Address/)).toHaveValue("");
  });

  it("writes a decide node's options one a line, as name: what it means", () => {
    const spy = jest.fn();
    render(<Harness which="decide" spy={spy} start={{ question: "", options: { yes: "", no: "" } }} />);
    fireEvent.change(screen.getByLabelText(/^Question/), { target: { value: "Does it need me today?" } });
    fireEvent.change(screen.getByLabelText(/^Options/), {
      target: { value: "urgent: needs an answer today\nlater: can wait\n" },
    });
    fireEvent.change(screen.getByLabelText(/^Sure enough at/), { target: { value: "0.8" } });
    expect(spy).toHaveBeenLastCalledWith({
      question: "Does it need me today?",
      options: { urgent: "needs an answer today", later: "can wait" },
      min_confidence: 0.8,
    });
  });
});
