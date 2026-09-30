"use client";

import { useEffect, useState } from "react";

import { Dialog } from "@/components/ui/dialog";

const KEYS: [string, string][] = [
  ["⌘ K", "Jump to a recording, speaker or page"],
  ["[", "Collapse or expand the navigation"],
  ["Space", "Play or pause (recording page)"],
  ["J / L", "Back or forward 10 seconds"],
  ["← / →", "Back or forward 5 seconds"],
  ["↑ / ↓", "Previous or next turn"],
  ["/", "Find in transcript"],
  ["?", "This list"],
];

/** "?" (or the account menu) opens the keyboard shortcuts. */
export function Shortcuts() {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (e.key === "?" && !["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName) && !t.isContentEditable) setOpen(true);
    };
    const onOpen = () => setOpen(true);
    window.addEventListener("keydown", onKey);
    window.addEventListener("lens:shortcuts", onOpen);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("lens:shortcuts", onOpen);
    };
  }, []);
  return (
    <Dialog open={open} onOpenChange={setOpen} title="Keyboard shortcuts">
      <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2.5 text-[14px]">
        {KEYS.map(([k, v]) => (
          <div key={k} className="contents">
            <dt>
              <kbd className="rounded-xs border border-border bg-surface px-1.5 py-0.5 font-sans text-[12.5px] font-semibold">
                {k}
              </kbd>
            </dt>
            <dd className="text-fg-secondary">{v}</dd>
          </div>
        ))}
      </dl>
    </Dialog>
  );
}
