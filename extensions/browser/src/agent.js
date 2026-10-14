// Injected into a tab the assistant reads or acts on (only sites the person allowed). The side panel calls it through
// scripting.executeScript; it keeps the numbers of what it has read in the extension's own world, for the life of
// the page, and nothing else.
import * as act from "./core/act.js";
import { describe, element, newRefs, read, secret } from "./core/snapshot.js";

if (!globalThis.__lensAgent) {
  const refs = newRefs();
  const find = (ref) => {
    const el = element(refs, ref);
    if (!el) throw new Error(`there's no ${ref} on this page now: read it again for current refs`);
    return el;
  };
  const answer = (fn) => {
    try {
      return { ok: true, result: fn() };
    } catch (e) {
      return { ok: false, error: e.message };
    }
  };
  globalThis.__lensAgent = {
    read: (opts) => answer(() => read(document, refs, opts)),
    /** What a ref is, and whether acting on it looks consequential, for the side panel to decide what to ask. */
    inspect: (ref, what = "click") =>
      answer(() => {
        const el = find(ref);
        return {
          ...describe(el),
          site: location.origin,
          secret: secret(el),
          refused: what === "click" ? null : act.refuseTyping(el),
          consequential: act.looksConsequential(el, what),
        };
      }),
    click: (ref) => answer(() => act.click(find(ref))),
    type: (ref, text, submit) =>
      answer(() => {
        const el = find(ref);
        const why = act.refuseTyping(el);
        if (why) throw new Error(why);
        const now = act.type(el, text);
        if (submit) act.submit(el);
        return now;
      }),
    /** What has the focus, and whether Enter there looks consequential. */
    focused: () =>
      answer(() => {
        const el = document.activeElement;
        if (!el || el === document.body) return null;
        return { ...describe(el), consequential: act.looksConsequential(el, "submit") };
      }),
    press: (key) => answer(() => act.press(document, key)),
  };
}
