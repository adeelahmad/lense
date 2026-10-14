// The options page: where clips go, the template, Markdown style and clipping choices, and what the assistant may do
// in this browser. Changes save as they're made.
import * as browserCtl from "../core/browser.js";
import * as settingsStore from "../core/settings.js";
import { check } from "../core/lens.js";
import { FILTERS, VARIABLES } from "../core/template.js";

const ext = globalThis.browser ?? globalThis.chrome;
const $ = (id) => document.getElementById(id);
let settings;

function saved(text = "Saved", kind = "ok") {
  $("saved").className = `status ${kind}`;
  $("saved").textContent = text;
  clearTimeout(saved.t);
  saved.t = setTimeout(() => ($("saved").textContent = ""), 2000);
}

async function patch(p) {
  Object.assign(settings, p);
  await settingsStore.save(p);
  saved();
}

const originPattern = (server) => `${new URL(server).origin}/*`;

// ---------- destinations ----------
function renderDestinations() {
  const box = $("destinations");
  box.replaceChildren(
    ...settings.destinations.map((d) => {
      const el = document.createElement("div");
      el.className = "dest";
      const isDefault = d.id === settings.defaultDestination || (!settings.defaultDestination && d === settings.destinations[0]);
      const row = document.createElement("div");
      row.className = "row";
      const name = document.createElement("div");
      name.className = "grow";
      const strong = document.createElement("strong");
      strong.textContent = d.name || "Lens";
      const where = document.createElement("div");
      where.className = "hint";
      where.textContent = d.server;
      name.append(strong, isDefault ? " (default)" : "", where);
      const state = document.createElement("span");
      state.className = "status";
      const button = (label, fn) => {
        const b = document.createElement("button");
        b.textContent = label;
        b.addEventListener("click", fn);
        return b;
      };
      row.append(
        name,
        button("Check", async () => {
          state.textContent = "Checking…";
          const r = await check({ server: d.server, token: await settingsStore.token(d.id) });
          state.className = `status ${r.ok ? "ok" : "bad"}`;
          state.textContent = r.message;
        }),
        ...(isDefault ? [] : [button("Make default", () => patch({ defaultDestination: d.id }).then(renderDestinations))]),
        button("Remove", async () => {
          await settingsStore.setToken(d.id, "");
          const destinations = settings.destinations.filter((x) => x.id !== d.id);
          await patch({ destinations, defaultDestination: isDefault ? destinations[0]?.id ?? "" : settings.defaultDestination });
          renderDestinations();
        }),
      );
      el.append(row, state);
      return el;
    }),
  );
}

$("add").addEventListener("click", () => {
  const out = $("addStatus");
  let server;
  try {
    server = settingsStore.normalizeServer($("newServer").value);
  } catch (e) {
    out.className = "status bad";
    out.textContent = e.message;
    return;
  }
  const token = $("newToken").value.trim();
  if (!token) {
    out.className = "status bad";
    out.textContent = "paste the import webhook's token";
    return;
  }
  // asked for straight away, while the click still counts as the user's: browsers only ask from a click
  const asked = ext.permissions.request({ origins: [originPattern(server)] });
  (async () => {
    if (!(await asked)) {
      out.className = "status bad";
      out.textContent = "the clipper needs your permission to talk to that address";
      return;
    }
    out.className = "status";
    out.textContent = "Checking…";
    const r = await check({ server, token });
    out.className = `status ${r.ok ? "ok" : "bad"}`;
    out.textContent = r.message;
    if (!r.ok) return;
    const id = crypto.randomUUID();
    await settingsStore.setToken(id, token);
    const destinations = [...settings.destinations, { id, name: $("newName").value.trim() || new URL(server).host, server }];
    await patch({ destinations, defaultDestination: settings.defaultDestination || id });
    $("newName").value = $("newServer").value = $("newToken").value = "";
    renderDestinations();
  })();
});

// ---------- templates and choices ----------
const TEXT = ["template", "selectionTemplate", "titleTemplate"];
const CHECKS = ["readability", "sendImages", "captureOnServer", "reviewQuickClips"];

function fill() {
  for (const k of TEXT) $(k).value = settings[k] ?? "";
  for (const k of CHECKS) $(k).checked = Boolean(settings[k]);
  $("maxImages").value = settings.maxImages;
  $("tags").value = (settings.tags ?? []).join(", ");
  for (const [k, v] of Object.entries(settings.markdown)) {
    const el = $(`md-${k}`);
    if (!el) continue;
    if (el.type === "checkbox") el.checked = Boolean(v);
    else el.value = v;
  }
}

for (const k of TEXT) $(k).addEventListener("change", () => patch({ [k]: $(k).value }));
for (const k of CHECKS) {
  $(k).addEventListener("change", () => {
    if (k === "sendImages" && $(k).checked) {
      ext.permissions.request({ origins: ["<all_urls>"] }).then((ok) => {
        if (!ok) $(k).checked = false;
        patch({ sendImages: ok });
      });
      return;
    }
    patch({ [k]: $(k).checked });
  });
}
$("maxImages").addEventListener("change", () => patch({ maxImages: Math.max(1, Math.min(19, Number($("maxImages").value) || 10)) }));
$("tags").addEventListener("change", () =>
  patch({
    tags: $("tags")
      .value.split(",")
      .map((t) => t.trim())
      .filter(Boolean),
  }),
);
for (const el of document.querySelectorAll("[id^='md-']")) {
  el.addEventListener("change", () => {
    const key = el.id.slice(3);
    patch({ markdown: { ...settings.markdown, [key]: el.type === "checkbox" ? el.checked : el.value } });
  });
}
$("resetTemplate").addEventListener("click", async () => {
  await patch({ template: settingsStore.DEFAULTS.template, selectionTemplate: "", titleTemplate: settingsStore.DEFAULTS.titleTemplate });
  fill();
});

$("vars").replaceChildren(
  ...Object.entries(VARIABLES).flatMap(([k, v]) => {
    const dt = document.createElement("dt");
    dt.textContent = `{${k}}`;
    const dd = document.createElement("dd");
    dd.textContent = v;
    return [dt, dd];
  }),
);
$("filters").textContent = `Filters: ${Object.keys(FILTERS).join(", ")}.`;

// ---------- the assistant ----------
async function renderSites() {
  const all = await browserCtl.sites();
  $("agentSites").replaceChildren(
    ...all.map((origin) => {
      const row = document.createElement("p");
      row.className = "row";
      const name = document.createElement("span");
      name.className = "grow";
      name.textContent = new URL(origin).host;
      const remove = document.createElement("button");
      remove.textContent = "Remove";
      remove.addEventListener("click", async () => {
        await browserCtl.forgetSite(origin);
        saved("Removed: the assistant asks again before using it");
        renderSites();
      });
      row.append(name, remove);
      return row;
    }),
  );
  if (!all.length) $("agentSites").replaceChildren(Object.assign(document.createElement("p"), { className: "hint", textContent: "None yet." }));
}

$("agentEnabled").addEventListener("change", async () => {
  await browserCtl.setEnabled($("agentEnabled").checked);
  saved();
});

(async () => {
  settings = await settingsStore.load();
  fill();
  renderDestinations();
  $("agentEnabled").checked = await browserCtl.enabled();
  renderSites();
})();
