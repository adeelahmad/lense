// The popup: clips the tab as soon as it opens (the selection if there is one, else the page), shows the Markdown to
// look over, and sends it.
import * as settingsStore from "../core/settings.js";
import { itemLink } from "../core/lens.js";
import { FILTERS } from "../core/template.js";

const ext = globalThis.browser ?? globalThis.chrome;
const $ = (id) => document.getElementById(id);
let tab;
let clip;

function status(text, kind = "") {
  $("status").className = `status ${kind}`;
  $("status").textContent = text;
}

function pressed(mode) {
  for (const b of document.querySelectorAll(".seg button")) b.setAttribute("aria-pressed", String(b.dataset.mode === mode));
}

function show(c) {
  clip = c;
  $("title").value = c.title;
  $("markdown").value = c.markdown;
  $("send").disabled = false;
  status(c.images?.length ? `${c.images.length} image${c.images.length > 1 ? "s" : ""} in this clip` : "");
}

async function run(mode, quiet = false) {
  pressed(mode);
  if (mode === "pick") {
    await ext.runtime.sendMessage({ type: "pick", tabId: tab.id });
    window.close(); // the page needs the pointer; the clip comes back here when it's picked
    return;
  }
  status("Reading the page…");
  const answer = await ext.runtime.sendMessage({ type: "clip", tabId: tab.id, mode });
  if (answer?.ok) return show(answer.clip);
  if (quiet) return false;
  status(/Cannot access|cannot be scripted|chrome:\/\/|extensions gallery/i.test(answer?.error ?? "")
    ? "The browser doesn't let extensions read this page."
    : answer?.error ?? "Couldn't read this page", "bad");
}

async function destinations() {
  const s = await settingsStore.load();
  const sel = $("destination");
  sel.replaceChildren(
    ...s.destinations.map((d) => {
      const o = document.createElement("option");
      o.value = d.id;
      o.textContent = d.name || d.server;
      o.selected = d.id === s.defaultDestination;
      return o;
    }),
  );
  if (!s.destinations.length) {
    $("send").disabled = true;
    status("Add your Lens in Options to send clips. Copy and Download work without it.");
  }
  return s;
}

const current = () => ({ ...clip, title: $("title").value.trim() || clip.title, markdown: $("markdown").value });

$("send").addEventListener("click", async () => {
  if (!clip) return;
  $("send").disabled = true;
  status("Sending…");
  const answer = await ext.runtime.sendMessage({ type: "send", clip: current(), destination: $("destination").value });
  $("send").disabled = false;
  if (!answer?.ok) return status(answer?.error ?? "Couldn't send it", "bad");
  const { server, namespace, items } = answer.answer;
  $("status").className = "status ok";
  $("status").replaceChildren(`Sent to ${namespace}. `);
  const first = items?.find((i) => i.kind === "text") ?? items?.[0];
  if (first) {
    const a = document.createElement("a");
    a.href = itemLink(server, first);
    a.target = "_blank";
    a.textContent = "Open in Lens";
    $("status").append(a);
  }
});

$("copy").addEventListener("click", async () => {
  await navigator.clipboard.writeText($("markdown").value);
  status("Copied", "ok");
});

$("download").addEventListener("click", () => {
  const blob = new Blob([$("markdown").value], { type: "text/markdown" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${FILTERS.filename($("title").value) || "clip"}.md`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
});

$("options").addEventListener("click", () => ext.runtime.openOptionsPage());
for (const b of document.querySelectorAll(".seg button")) b.addEventListener("click", () => run(b.dataset.mode));

(async () => {
  [tab] = await ext.tabs.query({ active: true, currentWindow: true });
  await destinations();
  await ext.action.setBadgeText({ tabId: tab.id, text: "" });
  const { pending } = await ext.storage.session.get("pending");
  if (pending && pending.tabId === tab.id && Date.now() - pending.at < 30 * 60 * 1000) {
    await ext.storage.session.remove("pending");
    pressed("pick");
    return show(pending.clip);
  }
  if ((await run("selection", true)) === false) await run("page");
})();
