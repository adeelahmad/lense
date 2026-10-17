"use client";

import { Check, Copy } from "lucide-react";
import { useState } from "react";

import { passageHref } from "@/components/chat/cite";
import { IconButton } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { cn } from "@/lib/utils";

/**
 * Copying from a conversation (docs/assistant.md, "Copy and export"): an answer as Markdown, its citations as
 * footnotes linking back to Lens like the Markdown export's, or as plain text; a question; a code block.
 */

/** A citation, archive or web: web pages (the assistant's web tools) carry their own address. */
export type Cited = {
  n: number;
  recording_id?: number | null;
  title?: string | null;
  t0?: number | null;
  time?: string | null;
  speaker?: string | null;
  text?: string | null;
  source?: string | null;
  url?: string | null;
  site?: string | null;
};

const GROUP = /\[(\d+(?:\s*,\s*\d+)*)\](?!\()/g; // [n] or [n, m], but not a link's text

const oneLine = (s: string | null | undefined) => (s ?? "").replace(/\s+/g, " ").trim();
const cut = (s: string | null | undefined, n = 280) => {
  const t = oneLine(s);
  return t.length <= n ? t : `${t.slice(0, n - 1).trimEnd()}…`;
};

/** Where a citation can be read: a web page's address, or the asset's page in Lens at its moment. */
export function citedLink(origin: string, p: Cited): string | null {
  if (p.source === "web" || p.url) return p.url ?? null;
  return p.recording_id == null ? null : origin + passageHref({ recording_id: p.recording_id, t0: p.t0 });
}

/** An answer as Markdown: [n] become footnotes naming the asset, its time and speaker, with a link back to it. */
export function answerMarkdown(text: string, passages: Cited[] | null | undefined, origin: string): string {
  const byN = new Map((passages ?? []).map((p) => [p.n, p]));
  const used = new Set<number>();
  const body = text.replace(GROUP, (whole, group: string) => {
    const ns = group.split(",").map((n) => Number(n.trim()));
    if (!ns.some((n) => byN.has(n))) return whole;
    return ns
      .map((n) => {
        if (!byN.has(n)) return `[${n}]`;
        used.add(n);
        return `[^${n}]`;
      })
      .join("");
  });
  const notes = [...used]
    .sort((a, b) => a - b)
    .map((n) => {
      const p = byN.get(n)!;
      const where = citedLink(origin, p);
      if (p.source === "web" || p.url) {
        const name = oneLine(p.title) || p.site || where || "Web page";
        return `[^${n}]: ${where ? `[${name}](${where})` : name}${p.site && p.site !== name ? `, ${p.site}` : ""}: “${cut(p.text)}”`;
      }
      const about = [oneLine(p.title) || "Untitled", p.time, p.speaker].filter(Boolean).join(", ");
      return `[^${n}]: ${about}: “${cut(p.text)}”${where ? ` <${where}>` : ""}`;
    });
  return [body.trim(), notes.join("\n")].filter(Boolean).join("\n\n") + "\n";
}

/** An answer as plain text: no Markdown marks, no citation numbers. */
export function plainText(text: string): string {
  return text
    .replace(/\s*\[(\d+(?:\s*,\s*\d+)*)\](?!\()/g, "")
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, "$1 ($2)")
    .replace(/^```[^\n]*$/gm, "")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

/** Write to the clipboard and say so; `what` names it ("Answer copied as Markdown"). Call it in the click itself:
 * some browsers only let a click write to the clipboard. */
export async function copyText(text: string, toast: ReturnType<typeof useToast>, what = "Copied"): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    toast({ title: what, tone: "green", duration: 2500 });
    return true;
  } catch {
    toast({
      title: "Couldn’t copy",
      body: "The browser didn’t allow it. Select the text and copy it instead.",
      tone: "red",
    });
    return false;
  }
}

/** The copy button on a code block: copies it as written, says "Copied". */
export function CopyCode({ text, className }: { text: string; className?: string }) {
  const toast = useToast();
  const [done, setDone] = useState(false);
  return (
    <IconButton
      label={done ? "Copied" : "Copy code"}
      size={28}
      onClick={async () => {
        if (!(await copyText(text, toast))) return;
        setDone(true);
        setTimeout(() => setDone(false), 2000);
      }}
      className={cn("bg-background/80 print:hidden [&_svg]:size-3.5", className)}
    >
      {done ? <Check /> : <Copy />}
    </IconButton>
  );
}

/** The copy button on a question: copies it as written. */
export function CopyQuestion({ text, className }: { text: string; className?: string }) {
  const toast = useToast();
  return (
    <IconButton
      label="Copy"
      size={32}
      onClick={() => void copyText(text, toast)}
      className={cn("print:hidden [&_svg]:size-4", className)}
    >
      <Copy />
    </IconButton>
  );
}
