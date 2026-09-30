"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Printer, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useSession } from "next-auth/react";
import { useEffect, useMemo, useRef, useState } from "react";

import { Admin, Jobs, Recordings, Templates } from "@/app/openapi-client";
import type { Player, Recording } from "@/app/openapi-client/types.gen";
import { downloadHtml, fetchReportHtml, htmlName, lightOnPrint, prepareReportHtml, printInLight } from "@/components/reports/report-html";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/field";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { Tooltip } from "@/components/ui/tooltip";
import { data, useApiClient } from "@/lib/api/browser";
import { shortDate, tc } from "@/lib/format";
import { needRole, useArchive } from "@/lib/hooks/session";

/** The printed page is always light, whatever the app theme: the light tokens, scoped to the sheet. */
const PAPER_CSS = `
.report-paper{--background:#FFFFFF;--surface:#F8F9FA;--surface-neutral:#F1F3F4;--text-primary:#202124;--text-strong:#3C4043;--text-secondary:#5F6368;--text-muted:#80868B;--border:#DADCE0;--accent-text:#1558B0;--aladdin-blue:#1A73E8;--spk-1:#1A73E8;--spk-2:#C5221F;--spk-3:#1E7E43;--spk-4:#B06000;--spk-5:#7A4E9A;--spk-6:#287F77;--spk-7:#4F5D75;--spk-8:#A23B5B;color-scheme:light;background:var(--background);color:var(--text-primary)}
@media print{
  @page{size:A4;margin:16mm 18mm}
  body *{visibility:hidden!important}
  .report-paper,.report-paper *{visibility:visible!important}
  .report-paper{position:absolute;left:0;top:0;width:100%!important;min-height:0!important;padding:0!important;box-shadow:none!important}
}`;

type Tpl = { value: string; label: string; template?: number; version?: number; url?: string; created?: string | null; name?: string };

const cap = "text-[11px] font-bold uppercase leading-none tracking-[.06em] text-fg-secondary";

/** The A4 sheet: the built-in recording report, drawn from the recording and its player data. */
function Paper({ rec, player }: { rec: Recording; player: Player }) {
  const stats = (rec.stats ?? {}) as { speakers?: { speaker_id: number; talk_ms: number; name?: string }[] };
  const order = new Map((player.speakers ?? []).map((s, i) => [Number((s as { id?: unknown }).id), i]));
  const talk = (stats.speakers ?? []).filter((s) => s.talk_ms > 0);
  const total = talk.reduce((a, s) => a + s.talk_ms, 0);
  const shares = talk
    .map((s) => ({ name: s.name ?? "Speaker", pct: total ? Math.round((s.talk_ms / total) * 100) : 0, color: `var(--spk-${((order.get(s.speaker_id) ?? 0) % 8) + 1})`, ms: s.talk_ms }))
    .sort((a, b) => b.ms - a.ms);
  const summary = (rec.summary ?? player.summary ?? null) as { summary?: string; action_items?: string[]; topics?: string[] } | null;
  const chapters = (player.sections ?? []) as { t0?: number; title?: string }[];
  const ents = ((player.entities ?? []) as { name?: string; segs?: unknown[] }[])
    .map((e) => ({ name: e.name ?? "", n: e.segs?.length ?? 0 }))
    .filter((e) => e.name)
    .sort((a, b) => b.n - a.n)
    .slice(0, 6);
  const keywords = ((player.keywords ?? []) as unknown[]).map((k) => (Array.isArray(k) ? String(k[0]) : String(k))).slice(0, 6);
  return (
    <article className="report-paper flex min-h-[1123px] w-[794px] max-w-full flex-col gap-[18px] px-[68px] py-16 font-sans shadow-[0_2px_12px_rgba(0,0,0,.14)] max-md:px-6 max-md:py-8">
      <div className="flex justify-between text-[11px] font-medium uppercase leading-none tracking-[.04em] text-fg-secondary">
        <span>Lens Archive · {rec.namespace}</span>
        <span>Recording report</span>
      </div>
      <h2 className="text-[28px] font-bold leading-[1.2] tracking-[-.015em]">{rec.title || "Untitled"}</h2>
      <p className="text-[12.5px] leading-normal text-fg-secondary">
        {[shortDate(player.recorded_at), player.duration_ms ? tc(player.duration_ms) : null].filter(Boolean).join(" · ")}
        {shares.length > 0 && " · "}
        {shares.map((s, i) => (
          <span key={s.name}>
            <span aria-hidden className="mr-1 inline-block size-2 rounded-full align-middle" style={{ background: s.color }} />
            {s.name} {s.pct}%{i < shares.length - 1 ? ", " : ""}
          </span>
        ))}
        {chapters.length > 0 && ` · ${chapters.length} ${chapters.length === 1 ? "chapter" : "chapters"}`}
      </p>
      {shares.length > 0 && (
        <div className="flex h-2 gap-0.5 overflow-hidden rounded-pill" role="img" aria-label={`Talk time: ${shares.map((s) => `${s.name} ${s.pct}%`).join(", ")}`}>
          {shares.map((s) => (
            <span key={s.name} style={{ flex: s.ms, background: s.color }} />
          ))}
        </div>
      )}
      <h3 className={cap}>Summary</h3>
      {summary?.summary ? (
        <p className="font-serif text-[13.5pt] leading-[1.55]">{summary.summary}</p>
      ) : (
        <p className="text-[12pt] leading-[1.45] text-fg-secondary">No summary yet. Summaries are written by a language model once one is set up in Settings.</p>
      )}
      <div className="grid grid-cols-2 gap-6">
        <div className="flex flex-col gap-1.5">
          <h3 className={cap}>{summary ? "Action items" : "Keywords"}</h3>
          {summary ? (
            summary.action_items?.length ? (
              summary.action_items.map((a) => (
                <span key={a} className="text-[12pt] leading-[1.45]">
                  ☐ {a}
                </span>
              ))
            ) : (
              <span className="text-[12pt] leading-[1.45] text-fg-secondary">None mentioned.</span>
            )
          ) : (
            <span className="text-[12pt] leading-[1.45]">{keywords.length ? keywords.join(" · ") : "—"}</span>
          )}
        </div>
        <div className="flex flex-col gap-1.5">
          <h3 className={cap}>Top entities</h3>
          <span className="text-[12pt] leading-[1.45]">{ents.length ? ents.map((e) => `${e.name} (${e.n})`).join(" · ") : "None found."}</span>
        </div>
      </div>
      {chapters.length > 0 && (
        <>
          <h3 className={cap}>Chapters</h3>
          <ol className="grid grid-cols-[50px_1fr] gap-x-3 gap-y-1.5 text-[12pt] leading-[1.35]">
            {chapters.map((c, i) => (
              <li key={i} className="contents">
                <span className="tabular text-fg-secondary">{tc(c.t0 ?? 0)}</span>
                <span>{c.title || `Chapter ${i + 1}`}</span>
              </li>
            ))}
          </ol>
        </>
      )}
      <span className="flex-1" />
      <div className="flex justify-between border-t border-border pt-2 text-[10px] leading-none text-fg-muted">
        <span>Printed {shortDate(new Date().toISOString())} · Recording report (built-in)</span>
        <span>Lens Archive</span>
      </div>
    </article>
  );
}

/** Reports RP2: the recording report as a printable A4 page, with the HTML export and a rebuild. */
export function RecordingReport({ id }: { id: number }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const token = useSession().data?.accessToken;
  const { admin, can } = useArchive();
  const frame = useRef<HTMLIFrameElement>(null);
  const [choice, setChoice] = useState("builtin");
  const [job, setJob] = useState<number | null>(null);
  useEffect(() => lightOnPrint(), []);

  const rec = useQuery({ queryKey: ["recording", id], queryFn: () => data(Recordings.getRecording({ client, path: { rid: id } })) });
  const player = useQuery({ queryKey: ["player", id], queryFn: () => data(Recordings.getPlayer({ client, path: { rid: id } })) });
  const outputs = useQuery({ queryKey: ["outputs", id], queryFn: () => data(Recordings.listOutputs({ client, path: { rid: id } })) });
  const templates = useQuery({ queryKey: ["templates"], queryFn: () => data(Templates.listTemplates({ client })), staleTime: 5 * 60_000 });
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => data(Admin.getSettings({ client })), enabled: admin, staleTime: 5 * 60_000 });
  const audioMode = (settings.data as Record<string, { values?: Record<string, unknown> }> | undefined)?.reports?.values?.audio;

  const options: Tpl[] = useMemo(() => {
    const outs = (outputs.data ?? []).filter((o) => o.key.startsWith("report_"));
    const list: Tpl[] = [{ value: "builtin", label: "Recording report (built-in)" }];
    for (const t of (templates.data ?? []).filter((x) => x.kind === "report")) {
      const out = outs.find((o) => Number((o.origin as { template?: unknown } | null)?.template) === t.id);
      const url = (out?.value as { url?: unknown } | null)?.url;
      list.push({
        value: `t${t.id}`,
        label: `${t.name} v${t.current}${out ? "" : " · not built yet"}`,
        template: t.id,
        version: Number((out?.origin as { version?: unknown } | null)?.version ?? t.current),
        url: typeof url === "string" ? url : undefined,
        created: out?.created_at,
        name: t.name,
      });
    }
    return list;
  }, [outputs.data, templates.data]);
  const tpl = options.find((o) => o.value === choice) ?? options[0];

  const tplHtml = useQuery({
    queryKey: ["report-html", tpl.url],
    queryFn: () => fetchReportHtml(tpl.url as string, token),
    enabled: Boolean(tpl.template && tpl.url),
    retry: false,
  });
  const srcDoc = useMemo(
    () => (tplHtml.data && typeof window !== "undefined" ? prepareReportHtml(tplHtml.data, { scripts: false, newTab: true, origin: window.location.origin }) : undefined),
    [tplHtml.data],
  );

  // Follow a rebuild until it's done, then refresh what it changed.
  const running = useQuery({
    queryKey: ["job", job],
    queryFn: () => data(Jobs.getJob({ client, path: { jid: job as number } })),
    enabled: job != null,
    refetchInterval: (q) => (q.state.data && !["queued", "running"].includes(q.state.data.status) ? false : 1500),
  });
  useEffect(() => {
    const st = running.data?.status;
    if (!st || ["queued", "running"].includes(st)) return;
    setJob(null);
    void qc.invalidateQueries({ queryKey: ["recording", id] });
    void qc.invalidateQueries({ queryKey: ["outputs", id] });
    void qc.invalidateQueries({ queryKey: ["report-html"] });
    toast(st === "succeeded" ? { title: "Report rebuilt", tone: "green" } : { title: "The report step didn’t finish", body: running.data?.error ?? st, tone: "red" });
  }, [running.data, qc, id, toast]);

  if (rec.isLoading || player.isLoading)
    return (
      <div className="flex flex-col gap-6 lg:flex-row" aria-busy="true" aria-label="Loading">
        <Skeleton className="h-[280px] w-full rounded-md lg:w-[300px]" />
        <Skeleton className="h-[600px] w-full max-w-[794px] rounded-sm" />
      </div>
    );
  if (rec.isError || player.isError || !rec.data || !player.data) {
    const err = (rec.error ?? player.error) as Error | null;
    return (
      <EmptyState
        tone="error"
        title="Couldn’t load this recording’s report"
        actions={
          <Button asChild variant="secondary">
            <Link href="/reports">Back to reports</Link>
          </Button>
        }
      >
        {err?.message ?? "It may have been removed, or you may not have access to its namespace."}
      </EmptyState>
    );
  }

  const r = rec.data;
  const canEdit = can("editor", r.namespace);
  const busy = job != null;
  const exportHtml = async () => {
    try {
      if (tpl.template) {
        if (!tplHtml.data) throw new Error("Build this report first.");
        downloadHtml(prepareReportHtml(tplHtml.data, { scripts: false, origin: window.location.origin }), htmlName(`${r.title}-${tpl.name}`));
      } else {
        if (!r.report_url) throw new Error("There’s no report page yet. The Report step builds it after analysis.");
        const page = await fetchReportHtml(r.report_url, token);
        downloadHtml(prepareReportHtml(page, { scripts: true, origin: window.location.origin }), htmlName(r.title));
      }
    } catch (e) {
      toast({ title: "Couldn’t export", body: (e as Error).message, tone: "red" });
    }
  };
  const print = () => {
    if (tpl.template) {
      const w = frame.current?.contentWindow;
      if (!w) return toast({ title: "Nothing to print yet", body: "Build this report first.", tone: "red" });
      w.focus();
      w.print();
    } else printInLight();
  };
  const rebuild = async () => {
    try {
      const res = await data(Recordings.reprocessRecording({ client, path: { rid: id }, body: { steps: [tpl.template ? { type: "report", template: tpl.template } : "report"] } }));
      setJob(res.job);
      toast({ title: "Rebuilding the report", body: "It runs in the background; this page updates when it’s done.", tone: "intent" });
    } catch (e) {
      toast({ title: "Couldn’t rebuild", body: (e as Error).message, tone: "red" });
    }
  };

  const note = tpl.template
    ? tpl.created
      ? `Built by the Report step · ${shortDate(tpl.created, true)} · template v${tpl.version}`
      : "Not built for this recording yet."
    : r.report_url
      ? `HTML page built by the Report step${typeof r.analyzed_at === "string" ? ` · analysed ${shortDate(r.analyzed_at, true).replace(/^Today, /, "today at ").replace(/^Yesterday, /, "yesterday at ")}` : ""}`
      : "No HTML page yet: the Report step builds it after analysis.";

  return (
    <div className="flex flex-col items-start gap-7 lg:flex-row">
      <style>{PAPER_CSS}</style>
      <aside className="flex w-full flex-col gap-3.5 rounded-md border border-border bg-background p-[18px] lg:sticky lg:top-20 lg:w-[300px] lg:shrink-0 print:hidden" aria-label="Report options">
        <label className="flex flex-col gap-1.5">
          <span className="text-[13px] font-bold text-fg-strong">Template</span>
          <Select value={choice} onChange={(e) => setChoice(e.target.value)} options={options.map((o) => ({ value: o.value, label: o.label }))} />
        </label>
        <div className="flex flex-col gap-1.5">
          <span className="text-[13px] font-bold text-fg-strong">Audio in report</span>
          <Tooltip content="Set for the whole archive in Settings → Reports (admins).">
            <span tabIndex={0} className="flex h-10 items-center rounded-sm border border-dashed border-border bg-surface px-3.5 text-[14px] capitalize text-fg-secondary">
              {typeof audioMode === "string" ? audioMode : "Set by an admin"}
            </span>
          </Tooltip>
        </div>
        <span className="text-[12px] leading-[1.45] text-fg-muted">
          Link points at the audio file, Embed puts it inside the HTML page, None leaves it out. The printed page never includes audio.
        </span>
        <div className="flex flex-col gap-2">
          <Button variant="primary" size="sm" icon={<Printer />} onClick={print} disabled={Boolean(tpl.template && !srcDoc)} disabledReason="Build this report first">
            Print / Save PDF
          </Button>
          <Button variant="secondary" size="sm" onClick={() => void exportHtml()} disabled={tpl.template ? !tplHtml.data : !r.report_url} disabledReason="There’s no HTML page yet — rebuild the report first">
            Export HTML
          </Button>
          <Button
            variant="ghost"
            size="sm"
            icon={<RefreshCw className={busy ? "animate-spin" : undefined} />}
            onClick={() => void rebuild()}
            disabled={!canEdit || busy}
            disabledReason={!canEdit ? needRole("editor", r.namespace) : "Rebuilding…"}
          >
            {busy ? "Rebuilding…" : tpl.template && !tpl.url ? "Build it" : "Regenerate"}
          </Button>
        </div>
        <span className="text-[12px] leading-[1.4] text-fg-muted">{note}</span>
      </aside>

      <div className="flex w-full min-w-0 justify-center lg:w-auto lg:justify-start">
        {tpl.template ? (
          tpl.url ? (
            tplHtml.isLoading ? (
              <Skeleton className="h-[600px] w-[794px] max-w-full rounded-sm" />
            ) : tplHtml.isError ? (
              <EmptyState tone="error" title="Couldn’t load this report" className="w-[794px] max-w-full">
                {(tplHtml.error as Error).message}
              </EmptyState>
            ) : (
              <iframe
                ref={frame}
                title={`${tpl.name} for ${r.title}`}
                srcDoc={srcDoc}
                sandbox="allow-same-origin allow-modals allow-popups allow-popups-to-escape-sandbox"
                className="h-[1123px] w-[794px] max-w-full bg-white shadow-[0_2px_12px_rgba(0,0,0,.14)]"
              />
            )
          ) : (
            <EmptyState title={`${tpl.name} isn’t built for this recording yet`} className="w-[794px] max-w-full rounded-md border border-dashed border-border">
              {canEdit ? "Build it with the button on the left; it runs as a Report step in the background." : `${needRole("editor", r.namespace)}.`}
            </EmptyState>
          )
        ) : (
          <Paper rec={r} player={player.data} />
        )}
      </div>
    </div>
  );
}

export function ReportBack({ ns }: { ns?: string | null }) {
  return (
    <Link href={ns ? `/reports?ns=${encodeURIComponent(ns)}` : "/reports"} className="inline-flex items-center gap-1 text-[13px] font-semibold text-fg-accent hover:underline print:hidden">
      <ArrowLeft className="size-4" aria-hidden />
      {ns ? `${ns} overview` : "Reports"}
    </Link>
  );
}
