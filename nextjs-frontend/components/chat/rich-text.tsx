import { Fragment, type ReactNode } from "react";

import { normClaim } from "@/components/chat/cite";
import { cn } from "@/lib/utils";

/**
 * Model-written text (chat answers, combined reports) rendered safely as React nodes: paragraphs, headings,
 * bullet and numbered lists, **bold** and `code`, with [n] citations handed to `renderCite`. Nothing is parsed as HTML.
 */

export type Block = { type: "p"; text: string } | { type: "h"; level: 1 | 2 | 3; text: string } | { type: "ul" | "ol"; items: string[] };

export function parseBlocks(text: string): Block[] {
  const out: Block[] = [];
  let para: string[] = [];
  let list: { type: "ul" | "ol"; items: string[] } | null = null;
  const flushPara = () => {
    if (para.length) out.push({ type: "p", text: para.join(" ").trim() });
    para = [];
  };
  const flushList = () => {
    if (list) out.push(list);
    list = null;
  };
  for (const raw of text.replace(/\r\n?/g, "\n").split("\n")) {
    const line = raw.trim();
    const h = /^(#{1,3})\s+(.*)$/.exec(line);
    const ul = /^[-*•]\s+(.*)$/.exec(line);
    const ol = /^\d+[.)]\s+(.*)$/.exec(line);
    if (!line) {
      flushPara();
      flushList();
    } else if (h) {
      flushPara();
      flushList();
      out.push({ type: "h", level: h[1].length as 1 | 2 | 3, text: h[2] });
    } else if (ul || ol) {
      flushPara();
      const type = ul ? "ul" : "ol";
      if (!list || list.type !== type) {
        flushList();
        list = { type, items: [] };
      }
      list.items.push((ul ?? ol)![1]);
    } else {
      flushList();
      // A line that starts with a citation ("[2] Episode 12, 0:06: …") is its own paragraph.
      if (/^\[\d+\]\s/.test(line)) flushPara();
      para.push(line);
    }
  }
  flushPara();
  flushList();
  return out;
}

type Cite = (n: number, key: string) => ReactNode;

function inline(text: string, cite: Cite, key: string): ReactNode[] {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`|\[\d+(?:\s*,\s*\d+)*\])/g);
  return parts.map((p, i) => {
    const k = `${key}-${i}`;
    if (!p) return null;
    if (p.startsWith("**") && p.endsWith("**") && p.length > 4) return <strong key={k}>{p.slice(2, -2)}</strong>;
    if (p.startsWith("`") && p.endsWith("`") && p.length > 2)
      return (
        <code key={k} className="rounded-xs bg-surface-neutral px-1 font-mono text-[0.85em]">
          {p.slice(1, -1)}
        </code>
      );
    const c = /^\[(\d+(?:\s*,\s*\d+)*)\]$/.exec(p);
    if (c)
      return (
        <Fragment key={k}>
          {c[1].split(",").map((n, j) => (
            <Fragment key={j}>
              {j > 0 && " "}
              {cite(Number(n.trim()), `${k}-${j}`)}
            </Fragment>
          ))}
        </Fragment>
      );
    return <Fragment key={k}>{p}</Fragment>;
  });
}

function matches(sentence: string, claims: string[]): boolean {
  const s = normClaim(sentence);
  return claims.some((c) => c === s || (c.length > 24 && (s.includes(c) || c.includes(s))));
}

/** A paragraph's sentences, with unsupported claims underlined (and announced as such). */
function sentencesOf(text: string, cite: Cite, key: string, unsupported: string[]): ReactNode[] {
  if (!unsupported.length) return inline(text, cite, key);
  return text.split(/(?<=[.!?])(\s+)/).map((s, i) => {
    if (!s.trim()) return s;
    if (!matches(s, unsupported)) return <Fragment key={`${key}-s${i}`}>{inline(s, cite, `${key}-s${i}`)}</Fragment>;
    return (
      <span key={`${key}-s${i}`} className="rounded-[3px] bg-red-surface underline decoration-red decoration-wavy decoration-[1.5px] underline-offset-4">
        <span className="sr-only">Unsupported claim: </span>
        {inline(s, cite, `${key}-s${i}`)}
      </span>
    );
  });
}

export function RichText({
  text,
  renderCite,
  unsupported = [],
  className,
  headingClassName,
}: {
  text: string;
  renderCite: Cite;
  /** Claims a source check found unsupported (their sentences get a red wavy underline). */
  unsupported?: string[];
  className?: string;
  headingClassName?: string;
}) {
  const claims = unsupported.map(normClaim).filter(Boolean);
  return (
    <div className={cn("flex flex-col gap-3", className)}>
      {parseBlocks(text).map((b, i) => {
        const key = `b${i}`;
        if (b.type === "h")
          return (
            <h3 key={key} className={cn("font-sans text-[15px] font-bold leading-snug text-fg", headingClassName)}>
              {inline(b.text, renderCite, key)}
            </h3>
          );
        if ("items" in b) {
          const List = b.type;
          return (
            <List key={key} className={cn("m-0 flex flex-col gap-1.5 pl-6", b.type === "ul" ? "list-disc" : "list-decimal")}>
              {b.items.map((it, j) => (
                <li key={j}>{sentencesOf(it, renderCite, `${key}-${j}`, claims)}</li>
              ))}
            </List>
          );
        }
        return (
          <p key={key} className="m-0">
            {sentencesOf(b.text, renderCite, key, claims)}
          </p>
        );
      })}
    </div>
  );
}
