"use client";

import { ChevronDown, ChevronUp, Loader2, Play, Sparkles, X } from "lucide-react";
import { useState } from "react";

import { Graph2 as GraphApi } from "@/app/openapi-client";
import { edgeKey, type Explorer } from "@/components/graph/explorer";
import { cellText, looksLikeCypher, type ApiEdge, type ApiNode } from "@/components/graph/model";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { cn } from "@/lib/utils";

type Result = {
  columns: string[];
  rows: unknown[][];
  nodes: ApiNode[];
  edges: ApiEdge[];
  truncated: boolean;
  ms?: number;
};
type Answer = { question?: string; cypher: string; explanation?: string | null; result: Result };

function nodeIn(v: unknown): string | null {
  if (v && typeof v === "object" && !Array.isArray(v)) {
    const o = v as Record<string, unknown>;
    if (typeof o.id === "string" && Array.isArray(o.labels)) return o.id;
  }
  return null;
}

/**
 * Ask the graph: a question in plain language (the language model writes the Cypher) or Cypher itself. The answer's
 * nodes join the canvas and light up; the query that found them is shown, to edit and run again.
 */
export function GraphAsk({ ex, scope, onSelect }: { ex: Explorer; scope: string; onSelect: (id: string) => void }) {
  const client = useApiClient();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [cypher, setCypher] = useState("");
  const [open, setOpen] = useState(true);

  const show = (a: Answer) => {
    setAnswer(a);
    setCypher(a.cypher);
    setOpen(true);
    const { ns, es } = ex.add(a.result.nodes, a.result.edges);
    ex.setHighlight(
      ns.length
        ? {
            title: `Answer: ${ns.length} ${ns.length === 1 ? "node" : "nodes"}`,
            nodes: new Set(ns.map((n) => n.id)),
            edges: new Set(es.map((e) => edgeKey(e.a, e.b))),
          }
        : null,
    );
  };

  const run = async (query: string, question?: string) => {
    setBusy(true);
    setError(null);
    try {
      if (question !== undefined) {
        const out = (await data(GraphApi.askGraph({ client, body: { question, scope } }))) as unknown as Answer;
        show(out);
      } else {
        const result = (await data(
          GraphApi.graphQuery({ client, body: { query, scope, limit: 500 } }),
        )) as unknown as Result;
        show({ cypher: query, question: answer?.question, explanation: null, result });
      }
    } catch (e) {
      const m = e instanceof Error ? e.message : String(e);
      setError(
        e instanceof ApiError && e.status === 409
          ? "No language model is set up for questions. Write Cypher instead (it starts with MATCH), or set one up in Settings."
          : m,
      );
    } finally {
      setBusy(false);
    }
  };

  const submit = () => {
    const t = text.trim();
    if (!t || busy) return;
    if (looksLikeCypher(t)) void run(t);
    else void run("", t);
  };

  const rows = answer?.result.rows ?? [];
  return (
    <div className="pointer-events-none absolute inset-x-3 bottom-3 z-10 flex flex-col items-stretch gap-2 pr-12 sm:items-start">
      {(answer || error) && open && (
        <section
          aria-label="Answer"
          className="pointer-events-auto flex max-h-[42dvh] w-full max-w-[720px] flex-col gap-2 overflow-hidden rounded-[12px] border border-border bg-background p-3 shadow-2"
        >
          <div className="flex items-start gap-2">
            <div className="min-w-0 flex-1 text-[13px] leading-snug">
              {answer?.question && <p className="m-0 font-semibold text-fg">{answer.question}</p>}
              {answer?.explanation && <p className="m-0 text-fg-secondary">{answer.explanation}</p>}
              {error && <p className="m-0 text-red-dark">{error}</p>}
            </div>
            <button
              type="button"
              aria-label="Hide the answer"
              onClick={() => setOpen(false)}
              className="grid size-7 shrink-0 place-items-center rounded-full hover:bg-surface-neutral"
            >
              <ChevronDown className="size-4" />
            </button>
            <button
              type="button"
              aria-label="Close the answer"
              onClick={() => {
                setAnswer(null);
                setError(null);
                ex.clearHighlight();
              }}
              className="grid size-7 shrink-0 place-items-center rounded-full hover:bg-surface-neutral"
            >
              <X className="size-4" />
            </button>
          </div>
          {answer && (
            <>
              <label className="flex flex-col gap-1">
                <span className="label-caps">Cypher used</span>
                <textarea
                  value={cypher}
                  onChange={(e) => setCypher(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                      e.preventDefault();
                      void run(cypher);
                    }
                  }}
                  rows={Math.min(5, cypher.split("\n").length + 1)}
                  spellCheck={false}
                  className="w-full resize-y rounded-sm border border-border bg-surface px-2 py-1.5 font-mono text-[12px] text-fg"
                />
              </label>
              <div className="flex items-center gap-2 text-[12px] text-fg-muted">
                <button
                  type="button"
                  onClick={() => void run(cypher)}
                  disabled={busy}
                  className="inline-flex h-7 items-center gap-1 rounded-[7px] border border-border px-2 font-semibold text-fg hover:bg-surface-neutral [&_svg]:size-3.5"
                >
                  <Play /> Run again
                </button>
                <span>
                  {rows.length} {rows.length === 1 ? "row" : "rows"}
                  {answer.result.truncated ? " (more not shown)" : ""}
                  {answer.result.ms !== undefined ? ` · ${answer.result.ms} ms` : ""}
                </span>
              </div>
              {rows.length > 0 && (
                <div className="min-h-0 overflow-auto rounded-sm border border-border">
                  <table className="w-full border-collapse text-[12.5px]">
                    <thead className="sticky top-0 bg-surface">
                      <tr>
                        {answer.result.columns.map((c) => (
                          <th key={c} scope="col" className="px-2 py-1 text-left font-semibold text-fg-secondary">
                            {c}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {rows.slice(0, 200).map((r, i) => (
                        <tr key={i} className="border-t border-border">
                          {r.map((v, j) => {
                            const id = nodeIn(v);
                            return (
                              <td key={j} className="max-w-[260px] truncate px-2 py-1 text-fg">
                                {id ? (
                                  <button
                                    type="button"
                                    onClick={() => onSelect(id)}
                                    className="text-fg-accent hover:underline"
                                  >
                                    {cellText(v)}
                                  </button>
                                ) : (
                                  cellText(v)
                                )}
                              </td>
                            );
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
        </section>
      )}
      {(answer || error) && !open && (
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="pointer-events-auto inline-flex h-8 items-center gap-1 self-start rounded-pill border border-border bg-background px-3 text-[12.5px] font-semibold text-fg shadow-1 [&_svg]:size-3.5"
        >
          <ChevronUp /> Show the answer
        </button>
      )}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
        className="pointer-events-auto flex w-full max-w-[720px] items-center gap-2 rounded-[12px] border border-border bg-background py-1 pl-3 pr-1 shadow-2"
      >
        <Sparkles className="size-4 shrink-0 text-fg-muted" aria-hidden />
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          aria-label="Ask the graph in plain words, or write Cypher"
          placeholder="Ask the graph (who talks about Acme?) or write Cypher (MATCH …)"
          className="h-9 min-w-0 flex-1 bg-transparent text-[13.5px] text-fg outline-none placeholder:text-fg-muted"
        />
        <span className={cn("hidden text-[11px] font-semibold text-fg-muted sm:inline", !text.trim() && "invisible")}>
          {looksLikeCypher(text) ? "Cypher" : "Question"}
        </span>
        <button
          type="submit"
          disabled={busy || !text.trim()}
          className="inline-flex h-9 items-center gap-1.5 rounded-[9px] bg-blue px-3 text-[13px] font-semibold text-white disabled:opacity-50 [&_svg]:size-4"
        >
          {busy ? <Loader2 className="animate-spin" /> : null} Ask
        </button>
      </form>
    </div>
  );
}
