# Clicks and keystrokes per feature

A survey of what Lens asks of people: it drives a fresh Lens in Chromium from the install wizard through setup and
each main feature, the way a person would with the defaults, and counts the clicks, keystrokes and passkey touches
each takes, per feature, per module and per page. `baseline.json` is the last accepted run; CI (the Clicks
workflow) fails a pull request whose journeys take more, so friction only goes down.

```sh
make clicks                                  # builds, then runs the survey and compares with the baseline
ci/clicks/run.sh --only chat-ask,note-create # some journeys (ids in journeys.mjs)
```

`run.sh` starts a Lens of its own on a temporary database and data folder (ports 3000, 8000, 3990 and 11434 must be
free), a stand-in OpenAI-compatible model on 11434 (`fake-model.mjs`, so the installer and the setup wizard find a
local model and the assistant answers), and the web installer with a stand-in for install.sh (`fake-install.sh`).
Results go to `results/clicks.json` and `results/clicks.md`.

## What counts

- **Clicks**: each mouse click. Typing into a field that doesn't have the focus adds the click that focuses it.
  Choosing from a native select is two; a file chooser is two (the button, then the file).
- **Keys**: each character typed, plus each key or shortcut pressed (Enter, Ctrl+K, Ctrl+S count one). A paste is one.
- **Prompts**: passkey touches.
- A step whose control is covered by something else (the floating Ask button) is still one click, marked
  "covered" in the steps.
- **Per page**: reach is clicks from Home through the sidebar (sections and tabs are two); controls are the buttons,
  links and fields in the page's main area on a fresh install, not counting navigation.

Journeys run in order on the same Lens, each from Home unless it continues the one before (setup does).

## When a change cuts clicks

Run the survey, check `results/clicks.md` shows the journey going down, and copy `results/clicks.json` over
`baseline.json` in the same pull request. A new journey goes at the end of `journeys.mjs` with an id that stays.
