"use client";

import { forwardRef, useId, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";

import { completionContext, completions, declaredNames, type Problem, type Variable } from "@/components/templates/template-model";
import { cn } from "@/lib/utils";

const LINE = 22.75; // 13px × 1.75
const PAD_Y = 14;

type Mark = { from: number; to: number; cls: string };

/** Colour {{ }} blue, {% %} gold, {# #} grey, and underline unknown variables in red. */
function highlight(text: string, problems: Problem[]): ReactNode[] {
  const marks: Mark[] = [];
  for (const m of text.matchAll(/\{\{[\s\S]*?\}\}|\{%[\s\S]*?%\}|\{#[\s\S]*?#\}/g)) {
    const s = m[0];
    marks.push({ from: m.index ?? 0, to: (m.index ?? 0) + s.length, cls: s.startsWith("{{") ? "text-[var(--term-blue)]" : s.startsWith("{%") ? "text-[var(--term-gold)]" : "text-term-muted" });
  }
  const errs = problems.map((p) => ({ from: p.index, to: p.index + p.length }));
  const cuts = new Set<number>([0, text.length]);
  for (const x of [...marks, ...errs]) {
    cuts.add(x.from);
    cuts.add(x.to);
  }
  const points = [...cuts].filter((n) => n >= 0 && n <= text.length).sort((a, b) => a - b);
  const out: ReactNode[] = [];
  for (let i = 0; i < points.length - 1; i++) {
    const a = points[i];
    const b = points[i + 1];
    if (a === b) continue;
    const mark = marks.find((m) => m.from <= a && b <= m.to);
    const err = errs.some((e) => e.from <= a && b <= e.to);
    out.push(
      <span key={a} className={cn(mark?.cls, err && "text-[var(--term-red)] underline decoration-[var(--term-red)] decoration-wavy underline-offset-[3px]")}>
        {text.slice(a, b)}
      </span>,
    );
  }
  return out;
}

export type CodeEditorHandle = { insert: (text: string) => void; focus: () => void };

/**
 * A plain-text code editor for templates: line numbers, {{ }} highlighting, unknown variables underlined, and
 * autocomplete after "{{" (↑/↓ to choose, Enter or Tab to insert, Esc to close). A transparent textarea sits over
 * a highlighted copy of the text, so typing, selection, undo and screen readers stay native.
 */
export const CodeEditor = forwardRef<CodeEditorHandle, { value: string; onChange: (v: string) => void; problems: Problem[]; readOnly?: boolean; label: string; autocomplete?: boolean }>(
  function CodeEditor({ value, onChange, problems, readOnly, label, autocomplete = true }, ref) {
    const area = useRef<HTMLTextAreaElement>(null);
    const measure = useRef<HTMLSpanElement>(null);
    const [charW, setCharW] = useState(7.8);
    const [ac, setAc] = useState<{ from: number; prefix: string; items: Variable[]; active: number } | null>(null);
    const listId = useId();
    const lines = value.split("\n").length;
    const declared = useMemo(() => declaredNames(value), [value]);

    useLayoutEffect(() => {
      if (measure.current) setCharW(measure.current.getBoundingClientRect().width / 10 || 7.8);
    }, []);

    const replace = (from: number, to: number, text: string) => {
      const el = area.current;
      if (!el) return;
      el.focus();
      el.setSelectionRange(from, to);
      // execCommand keeps the browser's undo history; fall back to setting the value.
      const ok = typeof document.execCommand === "function" && document.execCommand("insertText", false, text);
      if (!ok) {
        onChange(value.slice(0, from) + text + value.slice(to));
        requestAnimationFrame(() => el.setSelectionRange(from + text.length, from + text.length));
      }
    };

    useImperativeHandle(ref, () => ({
      insert: (text: string) => {
        const el = area.current;
        if (!el || readOnly) return;
        replace(el.selectionStart, el.selectionEnd, text);
      },
      focus: () => area.current?.focus(),
    }));

    const refresh = (text: string, caret: number) => {
      if (!autocomplete || readOnly) return setAc(null);
      const c = completionContext(text, caret);
      if (!c) return setAc(null);
      const items = completions(c.prefix, declared);
      setAc(items.length ? { ...c, items, active: 0 } : null);
    };

    const accept = (v: Variable) => {
      const el = area.current;
      if (!el || !ac) return;
      const caret = el.selectionStart;
      const rest = value.slice(caret);
      const closed = /^[^{]*?\}\}/.test(rest.split("\n")[0]);
      replace(ac.from, caret, v.key + (closed ? "" : " }}"));
      setAc(null);
    };

    const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (ac) {
        if (e.key === "ArrowDown" || e.key === "ArrowUp") {
          e.preventDefault();
          const d = e.key === "ArrowDown" ? 1 : -1;
          setAc({ ...ac, active: (ac.active + d + ac.items.length) % ac.items.length });
          return;
        }
        if (e.key === "Enter" || e.key === "Tab") {
          e.preventDefault();
          accept(ac.items[ac.active]);
          return;
        }
        if (e.key === "Escape") {
          e.preventDefault();
          setAc(null);
          return;
        }
      }
      if (e.key === "Tab" && !e.shiftKey && !readOnly) {
        // Two spaces, like the rest of the templates (Esc then Tab leaves the editor).
        e.preventDefault();
        replace(e.currentTarget.selectionStart, e.currentTarget.selectionEnd, "  ");
      }
    };

    // Where the caret is, for the autocomplete list.
    const caretPos = () => {
      const el = area.current;
      if (!el || !ac) return { top: 0, left: 0 };
      const before = value.slice(0, ac.from);
      const line = before.split("\n").length - 1;
      const col = before.length - before.lastIndexOf("\n") - 1;
      return { top: PAD_Y + (line + 1) * LINE + 2, left: 12 + col * charW };
    };
    const pos = caretPos();
    const activeId = ac ? `${listId}-${ac.active}` : undefined;

    return (
      <div className="relative min-h-0 flex-1 overflow-auto bg-term-bg font-mono text-[13px] leading-[1.75] text-term-fg">
        <span ref={measure} aria-hidden className="invisible absolute font-mono text-[13px]">
          0123456789
        </span>
        <div className="grid min-h-full grid-cols-[44px_minmax(0,1fr)]" style={{ paddingTop: PAD_Y, paddingBottom: PAD_Y }}>
          <div aria-hidden className="sticky left-0 z-[1] select-none bg-term-bg pr-3 text-right text-term-muted">
            {Array.from({ length: lines }, (_, i) => {
              const bad = problems.some((p) => p.line === i + 1);
              return (
                <div key={i} className={cn(bad && "text-[var(--term-red)]")}>
                  {i + 1}
                </div>
              );
            })}
          </div>
          <div className="relative w-max min-w-full">
            <pre aria-hidden className="m-0 min-h-full whitespace-pre pr-4 font-mono">
              {highlight(value, problems)}
              {"\n "}
            </pre>
            <textarea
              ref={area}
              aria-label={label}
              aria-autocomplete={autocomplete ? "list" : undefined}
              aria-controls={ac ? listId : undefined}
              aria-expanded={ac ? true : undefined}
              aria-activedescendant={activeId}
              aria-invalid={problems.length > 0 || undefined}
              role={autocomplete ? "combobox" : undefined}
              readOnly={readOnly}
              spellCheck={false}
              autoCapitalize="off"
              autoComplete="off"
              wrap="off"
              value={value}
              onChange={(e) => {
                onChange(e.target.value);
                refresh(e.target.value, e.target.selectionStart);
              }}
              onKeyDown={onKey}
              onClick={(e) => refresh(value, e.currentTarget.selectionStart)}
              onBlur={() => setTimeout(() => setAc(null), 150)}
              className="absolute inset-0 m-0 h-full w-full resize-none overflow-hidden whitespace-pre border-0 bg-transparent p-0 pr-4 font-mono text-[13px] leading-[1.75] text-transparent caret-[var(--term-fg)] outline-none selection:bg-[rgba(138,180,248,.35)] selection:text-transparent"
              style={{ WebkitTextFillColor: "transparent" }}
            />
          </div>
        </div>
        {ac && (
          <ul
            id={listId}
            role="listbox"
            aria-label="Variables"
            className="absolute z-10 w-[280px] rounded-[10px] bg-background p-1.5 font-sans text-[12.5px] text-fg shadow-3"
            style={{ top: pos.top, left: 44 + pos.left }}
          >
            {ac.items.map((v, i) => (
              <li
                key={v.key}
                id={`${listId}-${i}`}
                role="option"
                aria-selected={i === ac.active}
                onMouseDown={(e) => {
                  e.preventDefault();
                  accept(v);
                }}
                className={cn("flex cursor-pointer justify-between gap-2 rounded-[6px] px-2 py-2", i === ac.active && "bg-blue-surface")}
              >
                <code className="truncate font-mono text-[12px] font-medium">{v.key}</code>
                <span className="shrink-0 truncate text-fg-muted">{v.type.split(" · ")[0]}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    );
  },
);
