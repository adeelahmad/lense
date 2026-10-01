import "@testing-library/jest-dom";

import { HIGHLIGHT_NAME, HIGHLIGHT_RULE, installSpokenWordStyle } from "@/components/recording/spoken-word";

describe("the spoken-word highlight style", () => {
  it("is registered once, from the transcript, not from the stylesheet", () => {
    const style = installSpokenWordStyle(document);
    expect(style.tagName).toBe("STYLE");
    expect(style.textContent).toBe(HIGHLIGHT_RULE);
    expect(HIGHLIGHT_RULE).toContain(`::highlight(${HIGHLIGHT_NAME})`);
    expect(HIGHLIGHT_RULE).toContain("var(--hl-word)"); // the token, not a colour
    expect(installSpokenWordStyle(document)).toBe(style); // again: the same element
    expect(document.head.querySelectorAll("style[data-spoken-word]")).toHaveLength(1);
  });
});
