# Clicks, keystrokes and taps per feature

A survey of what Lens asks of people: it drives a fresh Lens in Chromium from the install wizard through setup and
each main feature, the way a person would with the defaults, and counts the clicks, keystrokes and passkey touches
each takes, per feature, per module and per page. It runs twice: on a computer (1400x900) and on a phone (Pixel 7,
412x839, touch), where clicks are taps, swipes are counted and there are no keyboard shortcuts. `baseline.json` and
`baseline-phone.json` are the last accepted runs; CI (the Clicks workflow) fails a pull request whose journeys take
more, so friction only goes down.

```sh
make clicks                                  # builds, then runs the survey on both and compares with the baselines
ci/clicks/run.sh --only chat-ask,note-create # some journeys (ids in journeys.mjs)
ci/clicks/run.sh --device phone --compare ci/clicks/baseline-phone.json
```

`run.sh` starts a Lens of its own on a temporary database and data folder (ports 3000, 8000, 3990 and 11434 must be
free), a stand-in OpenAI-compatible model on 11434 (`fake-model.mjs`, so the installer and the setup wizard find a
local model and the assistant answers), and the web installer with a stand-in for install.sh (`fake-install.sh`).
rclone must be on the PATH, as it is in the Lens image (the Clicks workflow copies the image's version). Results go to
`results/clicks.json` and `results/clicks.md` (`clicks-phone.*` for the phone), with the API's log beside them
(`backend.log`, `backend-phone.log`) and a screenshot of each journey that failed.

## What counts

- **Clicks**: each mouse click. Typing into a field that doesn't have the focus adds the click that focuses it.
  Choosing from a native select is two; a file chooser is two (the button, then the file).
- **Keys**: each character typed, plus each key or shortcut pressed (Enter, Ctrl+K, Ctrl+S count one). A paste is one.
- **Prompts**: passkey touches.
- **On a phone** a click is a tap, counted the same way. A **swipe** is counted when the control to tap or type into
  is off the screen (down the page, or along a strip that scrolls sideways) and has to be brought into view. Keyboard
  shortcuts don't exist there, so journeys use the buttons (Save, the Jump to box, the menu button for the
  navigation).
- **Outside the browser**: steps in the terminal, the assistant being connected or the browser's print dialog are
  counted on the page `(outside the browser)`.
- A step whose control is covered by something else (the floating Ask button) is still one click, marked
  "covered" in the steps.
- **Per page**: reach is clicks from Home through the sidebar (sections and tabs are two); controls are the buttons,
  links and fields in the page's main area on a fresh install, not counting navigation.

Journeys run in order on the same Lens, each from Home unless it continues the one before (setup does). A journey
that can't be done at all (on a phone, say, where a control is hidden) fails with the reason; it is in the baseline
as failed, so it doesn't turn CI red until it can be done, and then it counts as an improvement.

## When a change cuts clicks

Run the survey, check `results/clicks.md` (and `clicks-phone.md`) shows the journey going down, and copy
`results/clicks.json` over `baseline.json` (`clicks-phone.json` over `baseline-phone.json`) in the same pull
request; the Clicks workflow's `clicks` artifact has both from CI. A new journey goes at the end of `journeys.mjs`
with an id that stays.
