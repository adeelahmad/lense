// Injected into the tab when the user clips. It clips in place and answers the extension; it keeps nothing.
import { clip } from "./core/clip.js";
import { pick } from "./core/picker.js";

const ext = globalThis.browser ?? globalThis.chrome;

if (!globalThis.__lensClipper) {
  globalThis.__lensClipper = {
    /** Clip now: the page or the selection. */
    run({ mode, settings, tags }) {
      try {
        return { ok: true, clip: clip(document, { mode, settings, tags }) };
      } catch (e) {
        return { ok: false, error: e.message };
      }
    },
    /** Start the picker; the pick goes to the extension as a "picked" message, since the popup closes meanwhile. */
    async pick({ settings, tags, intent }) {
      const el = await pick(document);
      if (!el) return;
      let answer;
      try {
        answer = { ok: true, clip: clip(document, { mode: "element", element: el, settings, tags }) };
      } catch (e) {
        answer = { ok: false, error: e.message };
      }
      await ext.runtime.sendMessage({ type: "picked", intent, ...answer });
    },
  };
}
