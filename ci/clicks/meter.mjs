// Counts what a person does in the browser: clicks, keystrokes and passkey prompts, attributed to the page they're on.
//
// The rules, so runs compare:
// - click: one mouse click. Choosing from a native <select> is two (open it, pick); a file chooser is two (the button,
//   then picking the file in the system dialog). On a phone a click is a tap, counted the same way.
// - keys: every character typed, plus each key or shortcut pressed (Enter, Tab, Ctrl+K count one each). Pasting counts
//   one (the shortcut). Typing into a field that doesn't already have the focus adds the click that focuses it.
// - prompts: a passkey prompt (a touch or a PIN on the device), counted apart from clicks.
// - swipes (phone only): a control off the screen, down the page or along a strip that scrolls sideways, takes a swipe
//   to bring into view before the tap. On a computer scrolling isn't counted.

/** A page's address with the parts that differ per run (ids, codes, query values) taken out. */
export function pageKey(url) {
  if (!/^https?:/.test(url)) return "(outside the browser)";
  const u = new URL(url);
  const port = u.port === "3990" ? "installer" : "";
  const path = u.pathname
    .split("/")
    .map((s) => (/^\d+$|^[0-9a-f-]{8,}$|^[A-Za-z0-9_-]{20,}$/.test(s) ? ":id" : s))
    .join("/");
  return (port ? `${port}:` : "") + (path || "/");
}

export class Meter {
  /** `touch`: a phone, where clicks are taps and bringing a control into view takes a swipe. */
  constructor(page, { touch = false } = {}) {
    this.page = page;
    this.touch = touch;
    this.journey = null;
    this.journeys = [];
  }

  start(module, feature, note = "") {
    this.journey = { module, feature, note, clicks: 0, keys: 0, prompts: 0, swipes: 0, steps: [], started: Date.now() };
  }

  end(ok = true, error = null) {
    const j = this.journey;
    if (!j) return null;
    j.ms = Date.now() - j.started;
    delete j.started;
    j.ok = ok;
    if (error) j.error = String(error).split("\n")[0].slice(0, 300);
    this.journeys.push(j);
    this.journey = null;
    return j;
  }

  record(kind, what, clicks, keys, prompts = 0, swipes = 0) {
    const j = this.journey;
    if (!j) return;
    j.clicks += clicks;
    j.keys += keys;
    j.prompts += prompts;
    j.swipes += swipes;
    const step = { page: pageKey(this.page.url()), kind, what, clicks, keys, prompts };
    if (swipes) step.swipes = swipes;
    j.steps.push(step);
  }

  /** On a phone, 1 when the control is off the screen (a swipe brings it into view); otherwise 0. */
  async swipesTo(locator) {
    if (!this.touch) return 0;
    const box = await locator.boundingBox();
    const view = this.page.viewportSize();
    if (!box || !view) return 0;
    const off = box.y < 0 || box.y + box.height > view.height || box.x < 0 || box.x + box.width > view.width;
    return off ? 1 : 0;
  }

  /** A click, or a tap on a phone. */
  async tapOrClick(locator, options = {}) {
    return this.touch ? locator.tap(options) : locator.click(options);
  }

  async click(locator, what) {
    await locator.waitFor(); // on the page it belongs to, so the step is counted there
    this.record(this.touch ? "tap" : "click", what, 1, 0, 0, await this.swipesTo(locator));
    try {
      await this.tapOrClick(locator, { trial: true, timeout: 3000 });
      return await this.tapOrClick(locator);
    } catch {
      // Covered where it scrolled to: a person scrolls on to the end of the page (not counted) and tries again
      await this.page.mouse.wheel(0, 10000).catch(() => {});
      await this.page.waitForTimeout(300);
    }
    try {
      await this.tapOrClick(locator, { trial: true, timeout: 2000 });
    } catch {
      // Something still lies over it (the floating Ask button, a toast). The step notes it; the click still counts
      // one, done here with the keyboard so it reaches the control rather than what covers it.
      await locator.waitFor({ state: "visible" });
      const step = this.journey?.steps.at(-1);
      if (step) step.what += " (covered by something else on the page)";
      await locator.focus();
      await this.page.keyboard.press("Enter");
      return;
    }
    await this.tapOrClick(locator);
  }

  /** Types `text` into a field, adding the click that focuses it when it isn't focused already. */
  async type(locator, text, what) {
    await locator.waitFor();
    const focused = await locator.evaluate((el) => el === document.activeElement || el.contains(document.activeElement));
    this.record("type", what, focused ? 0 : 1, text.length, 0, focused ? 0 : await this.swipesTo(locator));
    if (!focused) await this.tapOrClick(locator);
    await locator.pressSequentially(text);
  }

  /** Pastes `text` into a field: the focusing click when needed, and the paste shortcut. */
  async paste(locator, text, what) {
    await locator.waitFor();
    const focused = await locator.evaluate((el) => el === document.activeElement || el.contains(document.activeElement));
    this.record("paste", what, focused ? 0 : 1, 1, 0, focused ? 0 : await this.swipesTo(locator));
    await locator.fill(text);
  }

  /** Replaces what a field holds: the focusing click when needed, Ctrl+A unless it's all selected, the new text. */
  async replace(locator, text, what) {
    await locator.waitFor();
    const { focused, selected } = await locator.evaluate((el) => ({
      focused: el === document.activeElement,
      selected: el === document.activeElement && el.value != null && el.selectionStart === 0 && el.selectionEnd === el.value.length,
    }));
    this.record("replace", what, focused ? 0 : 1, (selected ? 0 : 1) + text.length, 0, focused ? 0 : await this.swipesTo(locator));
    if (!focused) await this.tapOrClick(locator);
    if (!selected) await this.page.keyboard.press("ControlOrMeta+a");
    await locator.pressSequentially(text);
  }

  async press(key, what = key) {
    this.record("key", what, 0, 1);
    await this.page.keyboard.press(key);
  }

  /** Picks an option from a native select: open it, choose. */
  async select(locator, value, what) {
    await locator.waitFor();
    this.record("select", what, 2, 0, 0, await this.swipesTo(locator));
    await locator.selectOption(value);
  }

  /** Opens the system file chooser from `button` and picks `files`. */
  async upload(button, files, what) {
    this.record("upload", what, 2, 0, 0, await this.swipesTo(button));
    const [chooser] = await Promise.all([this.page.waitForEvent("filechooser"), this.tapOrClick(button)]);
    await chooser.setFiles(files);
  }

  /** A passkey prompt the browser shows (the virtual authenticator answers it). */
  passkey(what = "passkey prompt") {
    this.record("passkey", what, 0, 0, 1);
  }

  /** Arriving from a link the app or the installer printed, as a person pasting or clicking it would. */
  openLink(what) {
    this.record("link", what, 1, 0);
  }
}
