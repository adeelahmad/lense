"use client";

import { Loader2, Square, Volume2 } from "lucide-react";

import { IconButton } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { speakable, useReadAloud } from "@/lib/voice";

/** The play button on a chat message (docs/assistant.md, "Read a message aloud"): one click reads it aloud by the
 * text-to-speech model in the voice picked in Settings (else the browser), another stops it. */
export function ReadAloud({ text, size = 32, className }: { text: string; size?: number; className?: string }) {
  const r = useReadAloud(text);
  if (!speakable(text)) return null; // only code, say: nothing to read
  return (
    <IconButton
      label={r.on ? "Stop reading aloud" : "Read aloud"}
      size={size}
      active={r.on}
      onClick={r.toggle}
      className={cn("print:hidden [&_svg]:size-4", className, r.on && "opacity-100")}
    >
      {r.loading ? <Loader2 className="animate-spin" /> : r.on ? <Square /> : <Volume2 />}
    </IconButton>
  );
}
