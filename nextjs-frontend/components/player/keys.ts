/**
 * Player keyboard shortcuts (handoff: Space play/pause · J/L ±10 s · ←/→ ±5 s · ↑/↓ previous/next turn · / find;
 * video adds , and . for a frame, Shift+←/→ for the previous/next shot, C captions, F faces, T text on screen).
 * Pure: maps a key event to an action, or null when the key belongs to whatever has focus.
 */

export type PlayerAction =
  | { type: "toggle" }
  | { type: "seekBy"; ms: number }
  | { type: "turn"; dir: -1 | 1 }
  | { type: "find" }
  | { type: "frame"; dir: -1 | 1 }
  | { type: "shot"; dir: -1 | 1 }
  | { type: "overlay"; which: "captions" | "faces" | "text" };

export type KeyInput = {
  key: string;
  shiftKey?: boolean;
  metaKey?: boolean;
  ctrlKey?: boolean;
  altKey?: boolean;
  target?: EventTarget | null;
};

/** Widgets that take every key (menus, lists, grids, dialogs). */
const OWN_ALL = new Set(["menu", "menuitem", "menuitemcheckbox", "menuitemradio", "menubar", "listbox", "option", "combobox", "grid", "gridcell", "tree", "treeitem", "spinbutton", "dialog", "alertdialog"]);
/** Widgets that move with arrows (and Home/End/Page keys) but leave the player's letters alone. */
const OWN_ARROWS = new Set(["slider", "tab", "tablist", "radiogroup", "radio", "scrollbar"]);
const NAV_KEYS = new Set(["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End", "PageUp", "PageDown"]);

type ElementLike = { tagName?: string; isContentEditable?: boolean; getAttribute?: (n: string) => string | null; parentElement?: ElementLike | null };

/**
 * Whether focus is somewhere that owns the key: text entry always; menus and lists everything; sliders, tabs and radio
 * groups their arrow keys; buttons and links Space and Enter.
 */
export function focusOwnsKey(target: EventTarget | null | undefined, key: string): boolean {
  const el = target as ElementLike | null | undefined;
  if (!el || typeof el !== "object" || !el.tagName) return false;
  const tag = el.tagName.toUpperCase();
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable) return true;
  for (let n: ElementLike | null | undefined = el; n; n = n.parentElement) {
    if (n.getAttribute?.("data-player-keys") === "off") return true;
    const role = n.getAttribute?.("role");
    if (!role) continue;
    if (OWN_ALL.has(role)) return true;
    if (OWN_ARROWS.has(role) && NAV_KEYS.has(key)) return true;
  }
  if ((key === " " || key === "Enter") && (tag === "BUTTON" || tag === "A" || tag === "SUMMARY" || el.getAttribute?.("role") === "tab" || el.getAttribute?.("role") === "radio")) return true;
  return false;
}

export function keyToAction(e: KeyInput, mode: "audio" | "video" = "audio"): PlayerAction | null {
  if (e.metaKey || e.ctrlKey || e.altKey) return null;
  if (focusOwnsKey(e.target, e.key)) return null;
  const video = mode === "video";
  switch (e.key) {
    case " ":
    case "Spacebar":
      return { type: "toggle" };
    case "k":
    case "K":
      return video ? { type: "toggle" } : null;
    case "j":
    case "J":
      return { type: "seekBy", ms: -10_000 };
    case "l":
    case "L":
      return { type: "seekBy", ms: 10_000 };
    case "ArrowLeft":
      return video && e.shiftKey ? { type: "shot", dir: -1 } : { type: "seekBy", ms: -5_000 };
    case "ArrowRight":
      return video && e.shiftKey ? { type: "shot", dir: 1 } : { type: "seekBy", ms: 5_000 };
    case "ArrowUp":
      return e.shiftKey ? null : { type: "turn", dir: -1 };
    case "ArrowDown":
      return e.shiftKey ? null : { type: "turn", dir: 1 };
    case "/":
      return { type: "find" };
    case ",":
      return video ? { type: "frame", dir: -1 } : null;
    case ".":
      return video ? { type: "frame", dir: 1 } : null;
    case "c":
    case "C":
      return video ? { type: "overlay", which: "captions" } : null;
    case "f":
    case "F":
      return video ? { type: "overlay", which: "faces" } : null;
    case "t":
    case "T":
      return video ? { type: "overlay", which: "text" } : null;
    default:
      return null;
  }
}
