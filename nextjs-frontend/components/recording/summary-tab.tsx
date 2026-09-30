"use client";

import { FileCode2, RefreshCw, Sparkles } from "lucide-react";
import Link from "next/link";

import { usePlayerApi } from "@/components/player/media";
import { useRec } from "@/components/recording/context";
import { useOutputs, useRecordingActions, useTemplates } from "@/components/recording/hooks";
import { shortError } from "@/components/recording/jobs";
import { summaryDoc, type SummaryDoc } from "@/components/recording/summary-model";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/panel";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { absolute, relative, tc } from "@/lib/format";
import { needRole, useArchive } from "@/lib/hooks/session";

/**
 * Summary tab: the Summarize step's summary and every prompt-template output (Meeting notes and the like), each with
 * where it came from and Regenerate. Placeholders name the step that fills them; a failed Summarize repeats the error.
 */
export function SummaryTab() {
  const { rec, state, id, canEdit, ns } = useRec();
  const outputs = useOutputs(id);
  const templates = useTemplates();
  const { reprocess } = useRecordingActions(id);
  const { admin } = useArchive();
  const failed = state.phase === "failed" && (state.failedStep === "summarize" || state.failedStep === "llm");
  const summary = rec.summary && Object.keys(rec.summary).length ? rec.summary : null;
  const outs = outputs.data ?? [];
  const busy = state.phase === "processing" || state.phase === "analyzing";
  const regenReason = !canEdit ? needRole("editor", ns) : busy ? "A job is running for this recording" : undefined;

  if (outputs.isLoading) return <SkeletonLines />;

  return (
    <>
      {failed && (
        <div role="alert" className="flex flex-col items-start gap-3 rounded-lg border border-red-border bg-red-surface p-5">
          <div className="text-[15px] font-bold leading-snug text-red-dark">No summary yet</div>
          <p className="text-[13.5px] leading-normal text-fg-strong">
            The {state.failedStep === "llm" ? "template" : "Summarize"} step failed{state.error ? `: ${shortError(state.error)}` : ""}. Retrying keeps every earlier step&apos;s output — nothing is
            re-transcribed.
          </p>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="primary" disabled={!canEdit} disabledReason={needRole("editor", ns)} onClick={() => reprocess.mutate({ steps: ["summarize", "report"] })}>
              Retry
            </Button>
            {admin ? (
              <Button asChild size="sm" variant="secondary">
                <Link href="/settings">Open provider settings</Link>
              </Button>
            ) : (
              <Button size="sm" variant="secondary" disabled disabledReason="Only admins can change the AI provider">
                Open provider settings
              </Button>
            )}
          </div>
        </div>
      )}
      {summary && (
        <SummaryBlock
          doc={summaryDoc(summary)}
          source={
            <>
              <b className="font-semibold text-fg-strong">Summarize</b> step{rec.summarized_at ? ` · ${relative(rec.summarized_at)}` : ""}
            </>
          }
          sourceTitle={rec.summarized_at ? absolute(rec.summarized_at) : undefined}
          onRegenerate={() => reprocess.mutate({ steps: ["summarize", "report"] })}
          regenDisabled={regenReason}
          pending={reprocess.isPending}
        />
      )}
      {outs
        .filter((o) => !isFileOutput(o.key))
        .map((o) => {
          const origin = (o.origin ?? {}) as { template?: number; version?: number; model?: string };
          const t = templates.data?.find((x) => x.id === origin.template);
          return (
            <SummaryBlock
              key={o.key}
              doc={summaryDoc(o.value)}
              source={
                <>
                  Template <b className="font-semibold text-fg-strong">{t?.name ?? o.key}</b>
                  {origin.version != null && <span className="font-mono"> v{origin.version}</span>}
                  {origin.model ? ` · ${origin.model}` : ""}
                  {o.created_at ? ` · ${relative(o.created_at)}` : ""}
                </>
              }
              sourceTitle={o.created_at ? absolute(o.created_at) : undefined}
              onRegenerate={origin.template ? () => reprocess.mutate({ steps: [{ type: "llm", template: origin.template, key: o.key, ...(origin.model ? { model: origin.model } : {}) }] }) : undefined}
              regenDisabled={regenReason}
              pending={reprocess.isPending}
            />
          );
        })}
      {outs.some((o) => isFileOutput(o.key)) && (
        <section className="flex flex-col gap-1.5" aria-label="Reports and exports">
          <Label as="h3">Reports and exports</Label>
          <ul className="m-0 flex list-none flex-col p-0">
            {outs
              .filter((o) => isFileOutput(o.key))
              .map((o) => {
                const origin = (o.origin ?? {}) as { template?: number; version?: number };
                const t = templates.data?.find((x) => x.id === origin.template);
                const v = (o.value ?? {}) as { url?: string; file?: string; uploaded_to?: string };
                const report = o.key.startsWith("report_");
                return (
                  <li key={o.key} className="flex items-center gap-2 border-t border-border py-2 text-[13px] first:border-t-0">
                    <FileCode2 aria-hidden className="size-3.5 shrink-0 text-fg-muted" />
                    <span className="min-w-0 flex-1 truncate">
                      <b className="font-semibold">{t?.name ?? o.key}</b>
                      <span className="text-fg-muted">
                        {" "}
                        · {report ? "report page" : `file ${v.file ?? ""}${v.uploaded_to ? ` → ${v.uploaded_to}` : ""}`}
                        {o.created_at ? ` · ${relative(o.created_at)}` : ""}
                      </span>
                    </span>
                    {report && (
                      <Link href={`/reports?recording=${id}`} className="shrink-0 text-[12.5px] font-semibold text-fg-accent hover:underline">
                        Open in Reports
                      </Link>
                    )}
                  </li>
                );
              })}
          </ul>
        </section>
      )}
      {!summary && !outs.filter((o) => !isFileOutput(o.key)).length && !failed && (
        busy ? (
          <div className="flex flex-col gap-3 py-1">
            <p className="text-[13px] leading-[1.45] text-fg-secondary">
              The summary is written by the <b className="text-fg">Summarize</b> step, after Analyze. It appears here when that finishes.
            </p>
            <SkeletonLines still />
          </div>
        ) : (
          <EmptyState
            icon={<Sparkles />}
            title="No summary yet"
            className="py-10"
            actions={
              <Button size="sm" variant="secondary" icon={<Sparkles />} disabled={Boolean(regenReason)} disabledReason={regenReason} onClick={() => reprocess.mutate({ steps: ["summarize", "report"] })}>
                Run Summarize
              </Button>
            }
          >
            Summaries, action items and template outputs are written by the Summarize and template steps. They need an AI provider, set up by an admin in Settings.
          </EmptyState>
        )
      )}
    </>
  );
}

/** Report and export steps save where their file went, not text to read. */
function isFileOutput(key: string): boolean {
  return key.startsWith("report_") || key.startsWith("export_");
}

function SkeletonLines({ still }: { still?: boolean }) {
  return (
    <div className="flex flex-col gap-2" aria-hidden>
      {[90, 76, 84].map((w) => (
        <Skeleton key={w} className={still ? "h-2.5 !animate-none" : "h-2.5"} style={{ width: `${w}%` }} />
      ))}
    </div>
  );
}

function SummaryBlock({
  doc,
  source,
  sourceTitle,
  onRegenerate,
  regenDisabled,
  pending,
}: {
  doc: SummaryDoc;
  source: React.ReactNode;
  sourceTitle?: string;
  onRegenerate?: () => void;
  regenDisabled?: string;
  pending?: boolean;
}) {
  const { speakers } = useRec();
  const api = usePlayerApi();
  const colorOf = (who: string | null | undefined) => (who ? [...speakers.values()].find((s) => s.name.toLowerCase() === who.toLowerCase())?.color : undefined);
  return (
    <section className="flex flex-col gap-4">
      <div className="flex items-center gap-2 text-[12px] leading-snug text-fg-muted">
        <FileCode2 aria-hidden className="size-3.5 shrink-0" />
        <span className="flex-1" title={sourceTitle}>
          {source}
        </span>
        {onRegenerate && (
          <Button variant="ghost" size="sm" icon={<RefreshCw />} onClick={onRegenerate} disabled={Boolean(regenDisabled) || pending} disabledReason={regenDisabled}>
            Regenerate
          </Button>
        )}
      </div>
      {doc.empty && <p className="text-[13px] text-fg-muted">This output is empty.</p>}
      {doc.tldr && (
        <div className="flex flex-col gap-1.5">
          <Label as="h3">TL;DR</Label>
          <p className="m-0 font-serif text-[15.5px] leading-[1.55] text-fg [text-wrap:pretty]">{doc.tldr}</p>
        </div>
      )}
      {doc.sections.map((s) => (
        <div key={s.key} className="flex flex-col gap-[7px]">
          <Label as="h3">{s.title}</Label>
          <ul className="m-0 flex list-none flex-col gap-[7px] p-0">
            {s.items.map((it, i) => (
              <li key={i} className="grid grid-cols-[16px_1fr_auto] items-start gap-2 text-[13.5px] leading-[1.45] text-fg">
                <span aria-hidden className="text-center text-[12px] font-bold leading-[19px] text-fg-muted">
                  {s.glyph}
                </span>
                <span className="[text-wrap:pretty]">
                  {it.text}
                  {it.who && (
                    <span className="ml-1.5 inline-flex items-center gap-1 text-[11.5px] font-semibold leading-none text-fg-secondary">
                      <span aria-hidden className="size-[7px] rounded-[2px]" style={{ background: colorOf(it.who) ?? "var(--text-muted)" }} />
                      {it.who}
                    </span>
                  )}
                  {it.due && <span className="ml-1.5 text-[11.5px] text-fg-muted">due {it.due}</span>}
                </span>
                {it.t != null ? (
                  <button
                    type="button"
                    aria-label={`Play from ${tc(it.t)}`}
                    onClick={() => api.seek(it.t as number, { manual: true })}
                    className="tabular rounded-[6px] bg-surface-neutral px-[7px] py-1 text-[11.5px] font-semibold leading-none text-fg-strong hover:bg-border"
                  >
                    {tc(it.t)}
                  </button>
                ) : (
                  <span />
                )}
              </li>
            ))}
          </ul>
        </div>
      ))}
      {doc.chips.map((c) => (
        <div key={c.key} className="flex flex-col gap-2">
          <Label as="h3">{c.title}</Label>
          <div className="flex flex-wrap gap-1.5">
            {c.items.map((x, i) => (
              <span key={i} className="rounded-pill bg-surface-neutral px-2.5 py-1 text-[12.5px] font-medium text-fg-strong">
                {x}
              </span>
            ))}
          </div>
        </div>
      ))}
      {doc.facts.length > 0 && (
        <p className="text-[12.5px] text-fg-secondary">
          {doc.facts.map((f, i) => (
            <span key={f.label}>
              {i > 0 && " · "}
              {f.label}: <b className="font-semibold text-fg-strong">{f.value}</b>
            </span>
          ))}
        </p>
      )}
    </section>
  );
}
