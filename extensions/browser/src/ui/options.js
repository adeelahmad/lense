// The options page: where clips go, the template, Markdown style and clipping choices, and what the assistant may do
// in this browser. Changes save as they're made.
import * as account from "../core/account.js";
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
// Each is one Lens, reached as the person (signed in, or with their API token: clips go into the namespace picked here)
// or with an import webhook's token (clips go into the hook's namespace).
const KINDS = { signin: "Signed in", key: "API token", hook: "Import webhook" };

/** The token a destination sends: the person's sign-in for a signed-in one, else the one kept for it on this device. */
async function tokenFor(d) {
  if (d.kind !== "signin") return settingsStore.token(d.id);
  if ((await account.load())?.server !== d.server) return "";
  try {
    return await account.accessToken();
  } catch {
    return "";
  }
}

async function saveDestination(d) {
  await patch({ destinations: settings.destinations.map((x) => (x.id === d.id ? d : x)) });
}

/** What a check found, kept on the destination: where its clips can go. */
const placed = (d, target) => {
  if (!target || target.kind === "hook" || !target.namespaces?.length) return { ...d, namespace: target?.namespace ?? d.namespace };
  const namespace = target.namespaces.includes(d.namespace) ? d.namespace : (target.namespace ?? target.namespaces[0]);
  return { ...d, namespace, namespaces: target.namespaces };
};

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
      where.textContent = [d.server, KINDS[d.kind ?? "hook"], d.email].filter(Boolean).join(" · ");
      name.append(strong, isDefault ? " (default)" : "", where);
      const state = document.createElement("span");
      state.className = "status";
      const button = (label, fn) => {
        const b = document.createElement("button");
        b.type = "button";
        b.textContent = label;
        b.addEventListener("click", fn);
        return b;
      };
      const into = [];
      if ((d.namespaces?.length ?? 0) > 1) {
        const pick = document.createElement("select");
        pick.setAttribute("aria-label", `Namespace clips go into on ${d.name}`);
        pick.title = "The namespace clips go into (the popup lets you pick another)";
        for (const ns of d.namespaces) pick.append(new Option(ns, ns, false, ns === d.namespace));
        pick.addEventListener("change", () => saveDestination({ ...d, namespace: pick.value }).then(renderDestinations));
        into.push(pick);
      } else if (d.namespace) {
        const label = document.createElement("span");
        label.className = "hint";
        label.textContent = `into ${d.namespace}`;
        into.push(label);
      }
      row.append(
        name,
        ...into,
        button("Check", async () => {
          state.className = "status";
          state.textContent = "Checking…";
          const token = await tokenFor(d);
          if (!token) {
            state.className = "status bad";
            state.textContent = "Signed out: sign in again below";
            $("newServer").value = d.server;
            $("add").focus();
            return;
          }
          const r = await check({ server: d.server, token });
          state.className = `status ${r.ok ? "ok" : "bad"}`;
          state.textContent = r.message;
          if (r.ok && d.kind !== "hook") {
            await saveDestination(placed(d, r.target));
            renderDestinations();
          }
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

function addStatus(text, kind = "") {
  $("addStatus").className = `status grow ${kind}`;
  $("addStatus").textContent = text;
}

function showTokenRow(show) {
  $("tokenRow").hidden = !show;
  $("useToken").hidden = show || !account.canOAuth();
  $("add").textContent = !account.canOAuth() || $("newToken").value.trim() ? "Add and check" : "Sign in";
}

$("useToken").addEventListener("click", () => {
  showTokenRow(true);
  $("newToken").focus();
});
$("newToken").addEventListener("input", () => showTokenRow(true));

/** Add a Lens, or replace the one there was for the same address and kind (signing in again, a new token). */
async function addDestination(d, token) {
  const before = settings.destinations.find((x) => x.server === d.server && (x.kind ?? "hook") === d.kind && (d.kind !== "hook" || x.namespace === d.namespace));
  const id = before?.id ?? crypto.randomUUID();
  if (token) await settingsStore.setToken(id, token);
  const dest = { ...before, ...d, id };
  const destinations = before ? settings.destinations.map((x) => (x.id === id ? dest : x)) : [...settings.destinations, dest];
  await patch({ destinations, defaultDestination: settings.defaultDestination || id });
  renderDestinations();
}

$("addForm").addEventListener("submit", (e) => {
  e.preventDefault();
  let server;
  try {
    server = settingsStore.normalizeServer($("newServer").value);
  } catch (err) {
    addStatus(err.message, "bad");
    return;
  }
  const token = $("newToken").value.trim();
  if (!token && !account.canOAuth()) {
    addStatus("paste an API token with write access: in Lens, Settings → API keys", "bad");
    return;
  }
  // asked for straight away, while the click still counts as the user's: browsers only ask from a click
  const asked = ext.permissions.request({ origins: [originPattern(server)] });
  (async () => {
    if (!(await asked)) return addStatus("the clipper needs your permission to talk to that address", "bad");
    $("add").disabled = true;
    try {
      const name = new URL(server).host;
      if (token) {
        addStatus("Checking…");
        const r = await check({ server, token });
        addStatus(r.message, r.ok ? "ok" : "bad");
        if (!r.ok) return;
        const kind = r.target?.kind === "person" ? "key" : "hook";
        await addDestination(placed({ name, server, kind, email: r.target?.email ?? undefined }, r.target), token);
      } else {
        const me = await account.load();
        if (me?.server !== server || me.apiToken) {
          addStatus("Signing in: Lens asks you to allow the clipper…");
          await account.signIn(server);
        }
        const r = await check({ server, token: await account.accessToken() });
        addStatus(r.message, r.ok ? "ok" : "bad");
        if (!r.ok) return;
        await addDestination(placed({ name, server, kind: "signin", email: r.target?.email ?? undefined }, r.target));
      }
      $("newServer").value = $("newToken").value = "";
      showTokenRow(!account.canOAuth());
    } catch (err) {
      addStatus(`Couldn't sign in: ${err.message}`, "bad");
    } finally {
      $("add").disabled = false;
    }
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
  showTokenRow(!account.canOAuth());
  // the first thing left to do has focus: the address, or with a sign-in already here (the side panel's), Enter adds it
  const me = await account.load();
  if (me && !me.apiToken && !settings.destinations.some((d) => d.kind === "signin" && d.server === me.server)) $("newServer").value = me.server;
  if (!settings.destinations.length) ($("newServer").value ? $("add") : $("newServer")).focus();
  $("agentEnabled").checked = await browserCtl.enabled();
  renderSites();
})();
