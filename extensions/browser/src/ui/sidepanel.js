// The side panel: the Lens assistant next to any page (docs/browser-assistant.md). Sign in once, through Lens's own
// sign-in page, then ask. The conversation is the same one the web app shows, so it can be picked up there.
import * as account from "../core/account.js";
import * as assistant from "../core/assistant.js";
import * as settingsStore from "../core/settings.js";

const ext = globalThis.browser ?? globalThis.chrome;
const $ = (id) => document.getElementById(id);
const CHAT_KEY = "assistantChat"; // {server, id}: the conversation the side panel continues

let me = null; // the signed-in account
let chatId = null;
let answering = null; // {controller} while an answer streams

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  node.append(...children.filter((c) => c != null));
  return node;
}

// ---------- signing in ----------
function signinStatus(text, kind = "") {
  $("signinStatus").className = `status ${kind}`;
  $("signinStatus").textContent = text;
}

async function showSignIn(why = "") {
  me = null;
  chatId = null;
  for (const id of ["new", "open", "signout", "chat"]) $(id).hidden = true;
  $("signin").hidden = false;
  $("tokenRow").hidden = account.canOAuth();
  $("token").required = !account.canOAuth();
  if (!$("server").value) {
    const dest = settingsStore.destination(await settingsStore.load());
    if (dest) $("server").value = dest.server;
  }
  signinStatus(why, why ? "bad" : "");
  // the first thing left to fill has focus; with the address known, Enter signs in
  const next = !$("server").value ? $("server") : !$("tokenRow").hidden ? $("token") : $("signinButton");
  next.focus();
}

// browsers grant an extension a site only from the person's own click, so this is asked before anything is awaited
function askForSite(server) {
  if (!ext.permissions?.request) return Promise.resolve(true);
  return ext.permissions.request({ origins: [`${new URL(server).origin}/*`] });
}

$("signinForm").addEventListener("submit", (e) => {
  e.preventDefault();
  let server;
  try {
    server = settingsStore.normalizeServer($("server").value);
  } catch (err) {
    return signinStatus(err.message, "bad");
  }
  const allowed = askForSite(server);
  (async () => {
    $("signinButton").disabled = true;
    signinStatus("Signing in…");
    try {
      if (!(await allowed)) throw new Error(`allow the extension to reach ${new URL(server).host} when the browser asks`);
      me = account.canOAuth() ? await account.signIn(server) : await account.signInWithToken(server, $("token").value);
      $("token").value = "";
      signinStatus("");
      await showChat();
    } catch (err) {
      signinStatus(`Couldn't sign in: ${err.message}`, "bad");
    } finally {
      $("signinButton").disabled = false;
    }
  })();
});

$("signout").addEventListener("click", async () => {
  if (answering) await stopAnswer();
  await ext.storage.local.remove(CHAT_KEY);
  await account.signOut();
  $("log").replaceChildren();
  await showSignIn("");
});

// ---------- the conversation ----------
function scrollDown(force = false) {
  const log = $("log");
  if (force || log.scrollHeight - log.scrollTop - log.clientHeight < 80) log.scrollTop = log.scrollHeight;
}

function emptyHint() {
  if (!$("log").childElementCount) {
    $("log").append(el("p", { className: "empty", textContent: "Ask about anything in your Lens: it answers with its sources." }));
  }
}

function setChat(id) {
  chatId = id;
  $("open").href = id ? `${me.server}/chat/${id}` : `${me.server}/chat`;
  return ext.storage.local.set({ [CHAT_KEY]: { server: me.server, id } });
}

function addUser(text) {
  $("log").querySelector(".empty")?.remove();
  $("log").append(el("div", { className: "msg user", textContent: text }));
  scrollDown(true);
}

/** An answer in the log, filled in as it streams. */
function addAnswer() {
  $("log").querySelector(".empty")?.remove();
  const steps = el("ul", { className: "steps", hidden: true });
  const thinking = el("p", { className: "thinking", textContent: "Thinking…" });
  const body = el("div", { className: "body" });
  const root = el("div", { className: "msg assistant" }, steps, thinking, body);
  $("log").append(root);
  return {
    root,
    step(text) {
      steps.hidden = false;
      steps.append(el("li", { textContent: text }));
      scrollDown();
    },
    answer(text, passages) {
      thinking.remove();
      body.innerHTML = assistant.answerHtml(text, passages, me.server, window);
      scrollDown();
    },
    note(text, kind = "") {
      root.append(el("p", { className: `note ${kind}`, textContent: text }));
      scrollDown();
    },
    approval(a) {
      root.append(approvalCard(a));
      scrollDown();
    },
    done() {
      thinking.remove();
    },
  };
}

const DECIDED = { done: "Done", declined: "Declined" };

/** Work the assistant proposed, waiting for the person's pick (it never does it on its own). */
function approvalCard(a) {
  const status = el("span", { className: "status" });
  const buttons = el("p", { className: "row" });
  const decide = async (decision) => {
    for (const b of buttons.querySelectorAll("button")) b.disabled = true;
    status.className = "status";
    status.textContent = "…";
    try {
      const out = await assistant.decide(a.id, decision);
      buttons.replaceChildren(el("span", { className: "status ok", textContent: DECIDED[out.status] ?? out.status }));
    } catch (err) {
      if (err instanceof account.SignedOut) return showSignIn(err.message);
      status.className = "status bad";
      status.textContent = err.message;
      for (const b of buttons.querySelectorAll("button")) b.disabled = false;
    }
  };
  const approve = el("button", { className: "primary", type: "button", textContent: "Approve" });
  const decline = el("button", { type: "button", textContent: "Decline" });
  approve.addEventListener("click", () => decide("approve"));
  decline.addEventListener("click", () => decide("decline"));
  buttons.append(approve, decline, status);
  return el("div", { className: "approval" }, el("p", { textContent: a.summary || a.tool }), buttons);
}

function showMessage(m) {
  if (m.role === "user") return addUser(m.content);
  const view = addAnswer();
  for (const s of m.steps ?? []) view.step(s.summary || s.tool);
  if (m.content && m.content !== "(no answer)") view.answer(m.content, m.passages ?? []);
  if (m.notice) view.note(m.notice);
  if (m.stopped) view.note("Stopped");
  if (m.error) view.note(m.error, "bad");
  view.done();
}

async function showChat() {
  $("signin").hidden = true;
  for (const id of ["new", "open", "signout", "chat"]) $(id).hidden = false;
  $("signout").title = `Sign out of ${me.email ?? "Lens"} at ${new URL(me.server).host}`;
  $("log").replaceChildren();
  const { [CHAT_KEY]: saved } = await ext.storage.local.get(CHAT_KEY);
  await setChat(saved?.server === me.server ? saved.id : null);
  if (chatId) {
    try {
      const c = await assistant.getChat(chatId);
      for (const m of c.messages ?? []) showMessage(m);
      const waiting = await assistant.pendingApprovals(chatId);
      if (waiting.length) {
        const view = addAnswer();
        view.done();
        for (const a of waiting.reverse()) view.approval(a);
      }
    } catch (err) {
      if (err instanceof account.SignedOut) return showSignIn(err.message);
      if (err.status === 404) await setChat(null); // removed in Lens: start afresh
      else $("log").append(el("p", { className: "note bad", textContent: `Couldn't load the conversation: ${err.message}` }));
    }
  }
  emptyHint();
  scrollDown(true);
  $("q").focus();
}

$("new").addEventListener("click", async () => {
  if (answering) await stopAnswer();
  await setChat(null);
  $("log").replaceChildren();
  emptyHint();
  $("q").focus();
});

function setAnswering(on) {
  $("send").textContent = on ? "Stop" : "Send";
  $("send").classList.toggle("primary", !on);
  $("send").title = on ? "Stop the answer (Esc)" : "Send (Enter)";
}

async function stopAnswer() {
  if (!answering) return;
  try {
    await assistant.stop(chatId); // Lens keeps what came before and ends the stream
  } catch {
    answering?.controller.abort();
  }
}

async function send(text) {
  const view = { current: null };
  try {
    if (!chatId) await setChat(await assistant.newChat());
    addUser(text);
    view.current = addAnswer();
    answering = { controller: new AbortController() };
    setAnswering(true);
    let answer = "";
    let passages = [];
    await assistant.ask(chatId, text, {
      signal: answering.controller.signal,
      onEvent(name, data) {
        const v = view.current;
        if (name === "step") v.step(data.summary || data.tool);
        else if (name === "approval") v.approval(data);
        else if (name === "notice") v.note(data.message);
        else if (name === "passages") passages = Array.isArray(data) ? data : [];
        else if (name === "token") v.answer((answer += data.text ?? ""), passages);
        else if (name === "error") v.note(data.message, "bad");
        else if (name === "stopped") v.note("Stopped");
        else if (name === "cleared") v.note("The assistant starts afresh from here");
        else if (name === "compacted") v.note("The conversation so far is folded into a summary");
      },
    });
  } catch (err) {
    if (err instanceof account.SignedOut) return showSignIn(err.message);
    if (err.name === "AbortError") view.current?.note("Stopped");
    else if (view.current) view.current.note(err.message, "bad");
    else $("log").append(el("p", { className: "note bad", textContent: err.message }));
  } finally {
    view.current?.done();
    answering = null;
    setAnswering(false);
  }
}

$("ask").addEventListener("submit", (e) => {
  e.preventDefault();
  if (answering) return stopAnswer();
  const text = $("q").value.trim();
  if (!text) return;
  $("q").value = "";
  send(text).finally(() => $("q").focus());
});

$("q").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    if (!answering) $("ask").requestSubmit(); // the next question waits for this answer; Esc stops it
  }
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && answering) {
    e.preventDefault();
    stopAnswer();
  }
});

// ---------- start ----------
(async () => {
  me = await account.load();
  if (me) await showChat();
  else await showSignIn();
})().catch((err) => showSignIn(err.message));
