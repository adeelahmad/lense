import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";

import {
  citedNumbers,
  citeLabel,
  isNoModelAnswer,
  passageLines,
  quoteOf,
  sentences,
  shortTitle,
  splitCitations,
} from "@/components/chat/cite";
import { parseBlocks, RichText } from "@/components/chat/rich-text";
import { applyEvent, newTurn, stepCall } from "@/components/chat/stream";

describe("citations", () => {
  it("splits [n], [n, m] and [n][m]", () => {
    expect(splitCitations("A [1], B [2, 3][4].")).toEqual([
      { type: "text", text: "A " },
      { type: "cite", n: 1 },
      { type: "text", text: ", B " },
      { type: "cite", n: 2 },
      { type: "cite", n: 3 },
      { type: "cite", n: 4 },
      { type: "text", text: "." },
    ]);
    expect(citedNumbers("x [2] y [1] z [2]")).toEqual([2, 1]);
  });

  it("shortens titles for chips", () => {
    expect(shortTitle("Episode 12 — Reading a model system card")).toBe("Ep. 12");
    expect(shortTitle("Renewal call — Northwind Labs, Feb")).toBe("Renewal call");
    expect(shortTitle("A very long recording title without any separator at all")).toBe("A very long recording t…");
    expect(shortTitle(null)).toBe("Recording");
    expect(
      citeLabel({
        title: "Episode 12 — X",
        t0: 869000,
        time: "14:29",
        speaker: "Host B",
      }),
    ).toBe("Ep. 12 · 14:29 · Host B");
    expect(
      citeLabel({
        title: "Episode 12 — X",
        t0: 65000,
        time: null,
        speaker: null,
      }),
    ).toBe("Ep. 12 · 1:05");
  });

  it("reads passage lines and picks the cited one", () => {
    const p = {
      text: "Alice: Welcome back.\nBob: Wow, really?",
      speaker: "Bob",
    };
    expect(passageLines(p)).toEqual([
      { speaker: "Alice", text: "Welcome back." },
      { speaker: "Bob", text: "Wow, really?" },
    ]);
    expect(quoteOf(p).text).toBe("Wow, really?");
    expect(quoteOf({ text: "Just one line", speaker: "Host A" })).toEqual({
      speaker: "Host A",
      text: "Just one line",
    });
  });

  it("recognises the no-model fallback and splits sentences", () => {
    expect(isNoModelAnswer("No language model is configured, so here are the passages")).toBe(true);
    expect(sentences("One [1]. Two? Three!")).toEqual(["One [1].", "Two?", "Three!"]);
  });
});

describe("the answer stream", () => {
  it("builds a turn from server-sent events", () => {
    let s = newTurn("What did they promise?");
    s = applyEvent(s, {
      event: "step",
      data: '{"tool":"search_transcripts","args":{"query":"promise"},"summary":"Searched for \\"promise\\": 3 match(es)"}',
    });
    s = applyEvent(s, {
      event: "approval",
      data: '{"id":7,"tool":"run_template","summary":"Run Notes on 3 recording(s)","estimate":{"recordings":3}}',
    });
    s = applyEvent(s, {
      event: "passages",
      data: '[{"n":1,"recording_id":1,"text":"x"}]',
    });
    s = applyEvent(s, { event: "token", data: '{"text":"They "}' });
    s = applyEvent(s, { event: "token", data: '{"text":"promised SSO [1]."}' });
    s = applyEvent(s, { event: "done", data: '{"message":42}' });
    expect(s).toMatchObject({
      status: "done",
      text: "They promised SSO [1].",
      messageId: 42,
    });
    expect(s.steps).toHaveLength(1);
    expect(s.approvals[0]).toMatchObject({ id: 7, tool: "run_template" });
    expect(s.passages).toHaveLength(1);
    expect(stepCall(s.steps[0])).toBe('search_transcripts(query="promise")');
  });

  it("keeps an error through done, and ignores junk", () => {
    let s = newTurn("q");
    s = applyEvent(s, {
      event: "notice",
      data: '{"message":"This model can\'t use tools"}',
    });
    s = applyEvent(s, {
      event: "error",
      data: '{"message":"429 from the LLM server"}',
    });
    s = applyEvent(s, { event: "done", data: '{"message":5}' });
    s = applyEvent(s, { event: "ping", data: "not json" });
    expect(s).toMatchObject({
      status: "error",
      error: "429 from the LLM server",
      messageId: 5,
      notice: "This model can't use tools",
    });
  });
});

describe("rich text", () => {
  it("parses paragraphs, headings and lists", () => {
    expect(parseBlocks("# Themes\n\nFirst line\ncontinues.\n\n- a [1]\n- b\n1. c")).toEqual([
      { type: "h", level: 1, text: "Themes" },
      { type: "p", text: "First line continues." },
      { type: "ul", items: ["a [1]", "b"] },
      { type: "ol", items: ["c"] },
    ]);
    expect(parseBlocks("Intro:\n[1] Ep 12, 0:06: x\n[2] Ep 13, 0:00: y")).toHaveLength(3);
  });

  it("renders citations through the callback and marks unsupported claims", () => {
    render(
      <RichText
        text={"SSO by June [1]. A price hold through 2027 [2]. **Bold** <b>not html</b>"}
        renderCite={(n, key) => <button key={key}>cite {n}</button>}
        unsupported={["A price hold through 2027 [2]."]}
      />,
    );
    expect(screen.getByRole("button", { name: "cite 1" })).toBeInTheDocument();
    expect(screen.getByText("Unsupported claim:")).toBeInTheDocument();
    expect(screen.getByText("Bold").tagName).toBe("STRONG");
    expect(screen.getByText(/<b>not html<\/b>/)).toBeInTheDocument();
  });
});
