/**
 * The style of the word being said (the CSS Custom Highlight API's `::highlight(lens-word)`, painted by
 * transcript.tsx). It is registered from here, once per document, rather than in globals.css: the CSS parser the
 * bundler uses doesn't know the `::highlight()` pseudo-element, while browsers that have the API do.
 */
export const HIGHLIGHT_NAME = "lens-word";
export const HIGHLIGHT_RULE = `::highlight(${HIGHLIGHT_NAME}) { background-color: var(--hl-word); }`;
const MARK = "data-spoken-word";

/** Adds the rule to the document (once) and returns its style element. */
export function installSpokenWordStyle(doc: Document = document): HTMLStyleElement {
  const have = doc.head.querySelector<HTMLStyleElement>(`style[${MARK}]`);
  if (have) return have;
  const style = doc.createElement("style");
  style.setAttribute(MARK, "");
  style.textContent = HIGHLIGHT_RULE;
  doc.head.appendChild(style);
  return style;
}
