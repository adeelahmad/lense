import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Chats } from "@/app/openapi-client";
import {
  AT_REST,
  ASK_GAP,
  blockersUnder,
  controlAround,
  placeAsk,
  samplePoints,
  stuckBar,
  type Box,
} from "@/components/page-chat/ask-place";
import { PageChat } from "@/components/page-chat/page-chat";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("@/app/openapi-client", () => ({
  Chats: { createChat: jest.fn(), getChat: jest.fn(), chatCapabilities: jest.fn(), stopAnswer: jest.fn() },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("next/navigation", () => ({
  usePathname: () => "/import",
  useSearchParams: () => new URLSearchParams(""),
}));

// A 1400x900 screen; the button rests 20px from the bottom-right corner, 100x48.
const VIEW = 900;
const box = (top: number, bottom: number, left = 1280, right = 1380): Box => ({
  top,
  bottom,
  left,
  right,
  height: bottom - top,
});
const REST = box(VIEW - 68, VIEW - 20);
/** What getBoundingClientRect gives for a box at (x, y), w by h. */
const rectAt = (x: number, y: number, w: number, h: number) =>
  ({ x, y, left: x, top: y, width: w, height: h, right: x + w, bottom: y + h }) as DOMRect;

describe("placing the Ask button", () => {
  it("stays in its corner when nothing is under it", () => {
    expect(placeAsk(() => ({ box: REST, blockers: [] }), VIEW)).toEqual(AT_REST);
  });

  it("rises above a row of buttons it would cover, with a gap", () => {
    const row = box(VIEW - 70, VIEW); // a sticky Import row, 70px tall
    const seen: number[] = [];
    const place = placeAsk((lift) => {
      seen.push(lift);
      const at = box(REST.top - lift, REST.bottom - lift);
      return { box: at, blockers: at.bottom > row.top ? [row] : [] };
    }, VIEW);
    expect(place).toEqual({ lift: REST.bottom - row.top + ASK_GAP, hidden: false });
    expect(seen).toEqual([0, 62]); // and checks it's clear up there
  });

  it("keeps rising over what it covers next", () => {
    const row = box(VIEW - 70, VIEW);
    const field = box(VIEW - 130, VIEW - 90); // a field just above the row
    const place = placeAsk((lift) => {
      const at = box(REST.top - lift, REST.bottom - lift);
      return { box: at, blockers: [row, field].filter((b) => at.bottom > b.top && at.top < b.bottom) };
    }, VIEW);
    expect(place).toEqual({ lift: REST.bottom - field.top + ASK_GAP, hidden: false });
  });

  it("steps out of sight rather than rise past the middle of the screen", () => {
    const panel = box(VIEW / 2 - 40, VIEW); // controls reaching up past the middle
    expect(placeAsk(() => ({ box: REST, blockers: [panel] }), VIEW)).toEqual({ lift: 0, hidden: true });
  });

  it("samples the whole of its area", () => {
    const pts = samplePoints(REST);
    expect(pts).toHaveLength(15);
    expect(pts[0]).toEqual([REST.left + 3, REST.top + 3]);
    expect(pts[14]).toEqual([REST.right - 3, REST.bottom - 3]);
  });
});

describe("what counts as covered", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("knows controls, and clickable rows by their pointer", () => {
    // (the word's pointer is the row's, inherited in a browser; jsdom doesn't inherit it)
    document.body.innerHTML = `<div id="row" style="cursor: pointer"><span id="word" style="cursor: pointer">Row</span></div>
      <button id="b"><svg id="icon"></svg></button><p id="text">Words</p><label id="l">Name <input id="i" /></label>`;
    const el = (id: string) => document.getElementById(id)!;
    expect(controlAround(el("icon"))).toBe(el("b"));
    expect(controlAround(el("word"))).toBe(el("row"));
    expect(controlAround(el("i"))).toBe(el("i"));
    expect(controlAround(el("l"))).toBeNull(); // the empty end of a label isn't somewhere people tap
    expect(controlAround(el("text"))).toBeNull();
  });

  it("finds the bar a control stays put in: fixed, or sticky to the bottom and stuck there", () => {
    document.body.innerHTML = `<div id="fixed" style="position: fixed"><button id="a">A</button></div>
      <div id="top" style="position: sticky; top: 0"><button id="b">B</button></div>
      <div id="bottom" style="position: sticky; bottom: 0"><button id="c">C</button></div>`;
    const el = (id: string) => document.getElementById(id)!;
    el("bottom").getBoundingClientRect = () => rectAt(0, VIEW - 60, 1400, 60);
    Object.defineProperty(window, "innerHeight", { value: VIEW, configurable: true });
    expect(stuckBar(el("a"))).toBe(el("fixed"));
    expect(stuckBar(el("b"))).toBeNull();
    expect(stuckBar(el("c"))).toBe(el("bottom"));
    // the same sticky row sitting up the page, where scrolling still moves it, isn't stuck
    el("bottom").getBoundingClientRect = () => rectAt(0, 300, 1400, 60);
    expect(stuckBar(el("c"))).toBeNull();
  });

  it("covers only what's drawn under the button, and what scrolling can't carry clear", () => {
    document.body.innerHTML = `<span id="probe"></span><div id="bar" style="position: fixed; bottom: 0">
      <button id="import">Import</button></div><button id="mid">Row action</button><div id="toast"><button>Undo</button></div>`;
    const el = (id: string) => document.getElementById(id)!;
    const rect = (top: number, h: number) => () => rectAt(1200, top, 200, h);
    el("bar").getBoundingClientRect = rect(VIEW - 70, 70);
    el("import").getBoundingClientRect = rect(VIEW - 56, 40);
    el("mid").getBoundingClientRect = rect(VIEW - 60, 40);
    Object.defineProperty(window, "innerHeight", { value: VIEW, configurable: true });
    const probe = el("probe");
    const under = (stack: Element[]) => {
      document.elementsFromPoint = () => stack;
      return blockersUnder(probe, REST);
    };
    // the sticky row: the button clears the whole row
    expect(under([probe, el("import"), el("bar"), document.body])).toEqual([
      { top: VIEW - 70, bottom: VIEW, left: 1200, right: 1400, height: 70 },
    ]);
    // drawn over the button (a toast): not its business
    expect(under([el("toast").firstElementChild!, probe, document.body])).toEqual([]);
    // a button in the page with more page below it: scrolling carries it clear
    Object.defineProperty(document.documentElement, "scrollHeight", { value: 3000, configurable: true });
    Object.defineProperty(document.documentElement, "clientHeight", { value: VIEW, configurable: true });
    expect(under([probe, el("mid"), document.body])).toEqual([]);
    // the same button with the page at its end: it's covered
    Object.defineProperty(document.documentElement, "scrollHeight", { value: VIEW, configurable: true });
    expect(under([probe, el("mid"), document.body])).toEqual([
      { top: VIEW - 60, bottom: VIEW - 20, left: 1200, right: 1400, height: 40 },
    ]);
  });
});

describe("the Ask button on a page", () => {
  const original = document.elementsFromPoint;
  afterEach(() => {
    document.elementsFromPoint = original;
    localStorage.clear();
  });

  it("rises above the page's sticky Import row, and settles back when it goes", async () => {
    (Chats.chatCapabilities as jest.Mock).mockReturnValue(new Promise(() => {}));
    Object.defineProperty(window, "innerHeight", { value: VIEW, configurable: true });
    let rowShown = true;
    const qc = new QueryClient();
    const view = render(
      <QueryClientProvider client={qc}>
        <TooltipProvider>
          <main id="main">
            <div data-testid="row" style={{ position: "sticky", bottom: 0 }}>
              <button type="button">Import 2 files</button>
            </div>
          </main>
          <PageChat />
        </TooltipProvider>
      </QueryClientProvider>,
    );
    const row = screen.getByTestId("row");
    const importButton = screen.getByRole("button", { name: "Import 2 files" });
    row.getBoundingClientRect = () => rectAt(0, VIEW - 70, 1400, 70);
    importButton.getBoundingClientRect = () => rectAt(1240, VIEW - 55, 140, 40);
    const ask = screen.getByRole("button", { name: "Chat about this page" });
    const probe = ask.previousElementSibling as HTMLElement;
    // the probe sits where the button would be, raised by whatever it's been moved by
    probe.getBoundingClientRect = () => {
      const lift = -parseFloat((probe.style.translate || "0 0px").split(" ")[1]);
      return rectAt(1280, VIEW - 68 - lift, 100, 48);
    };
    document.elementsFromPoint = (_x: number, y: number) =>
      rowShown && y >= VIEW - 70 ? [probe, importButton, row, document.body] : [probe, document.body];

    act(() => window.dispatchEvent(new Event("resize")));
    await act(() => new Promise((r) => requestAnimationFrame(r)));
    expect(ask).toHaveAttribute("data-place", "raised");
    expect(ask.style.translate).toBe(`0 -${VIEW - 20 - (VIEW - 70) + ASK_GAP}px`);
    expect(ask).toBeVisible();

    rowShown = false;
    act(() => window.dispatchEvent(new Event("scroll")));
    await act(() => new Promise((r) => requestAnimationFrame(r)));
    expect(ask).toHaveAttribute("data-place", "rest");
    expect(ask.style.translate).toBe("");
    view.unmount();
  });
});
