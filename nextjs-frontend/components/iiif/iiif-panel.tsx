"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, ChevronDown, ChevronRight, Copy, ExternalLink, Lock, Share2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Iiif, Metadata } from "@/app/openapi-client";
import { isUnreachable } from "@/components/errors/error-states";
import { includedFrom, momentLabel, parseClock, schemaProblem } from "@/components/iiif/iiif-model";
import { FIELD_ANCHOR } from "@/components/iiif/metadata-editor";
import {
  ACCESS,
  FIELD_LABEL,
  profileProblems,
  PUBLISH_BADGE,
  publishState,
  type Field,
  type Problem,
} from "@/components/iiif/metadata-model";
import {
  keys,
  useNamespaceMeta,
  useRecordingBrief,
  useRecordingIiif,
  useRecordingMeta,
} from "@/components/iiif/queries";
import { ChoiceCards, SegmentedChoice } from "@/components/settings/controls";
import { Badge } from "@/components/ui/badge";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { needRole, useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

/**
 * The IIIF panel of a recording (IIIF Publishing IP1): its Manifest URL, "Open in" viewers, what's published, the
 * access level, validation results, sharing a moment as a Content State link, and the manifest JSON.
 * The same panel serves the Recording page's IIIF tab and the IIIF section of Share.
 */
export function IiifPanel({ recordingId }: { recordingId: number }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const { can, admin } = useArchive();
  const panel = useRecordingIiif(recordingId);
  const meta = useRecordingMeta(recordingId);
  const rec = useRecordingBrief(recordingId);
  const ns = rec.data?.namespace ?? null;
  const profile = useNamespaceMeta(ns).data?.profile;
  const [jsonOpen, setJsonOpen] = useState(false);

  const setAccess = useMutation({
    mutationFn: (access: string) =>
      data(
        Metadata.updateRecordingMetadata({
          client,
          path: { rid: recordingId },
          body: { set: { access } },
        }),
      ),
    onSuccess: (r, access) => {
      qc.setQueryData(keys.meta(recordingId), r);
      void qc.invalidateQueries({ queryKey: keys.iiif(recordingId) });
      void qc.invalidateQueries({ queryKey: keys.history(recordingId) });
      toast({
        title: access === "private" ? "Unpublished" : "Access changed",
        body: ACCESS.find((a) => a.value === access)?.anon,
        tone: "green",
      });
    },
    onError: (e) => toast({ title: "Couldn’t change access", body: e.message, tone: "red" }),
  });

  if (panel.isPending)
    return (
      <div className="flex flex-col gap-3.5 px-[18px] py-4" aria-busy="true" aria-label="Loading IIIF">
        <Skeleton className="h-5 w-24" />
        <Skeleton className="h-11 w-full" />
        <Skeleton className="h-8 w-2/3" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  if (panel.isError)
    return (
      <EmptyState
        tone="error"
        icon={<Share2 />}
        title={isUnreachable(panel.error) ? "Can’t reach the server" : "Couldn’t load the IIIF details"}
        actions={<Button onClick={() => panel.refetch()}>Try again</Button>}
      >
        {panel.error.message}
      </EmptyState>
    );

  const p = panel.data;
  const metaProblems: Problem[] = meta.data
    ? meta.data.problems.length
      ? meta.data.problems
      : profileProblems(meta.data.meta, profile, ns)
    : [];
  const schema = (p.validation.problems ?? []).map(schemaProblem);
  const problemCount = metaProblems.length + schema.length;
  const state = publishState(p.access, problemCount);
  const badge = PUBLISH_BADGE[state];
  const included = includedFrom(p.json);
  const canPublish = can("owner", ns);
  const current = ACCESS.find((a) => a.value === p.access) ?? ACCESS[3];

  return (
    <div className="flex flex-col gap-3.5 px-[18px] py-4">
      <div className="flex items-center gap-2">
        <h2 className="flex-1 text-[15px] font-bold leading-none text-fg">IIIF</h2>
        <Badge tone={badge.tone} dot>
          {badge.label}
        </Badge>
      </div>

      <div className="flex items-center gap-2.5">
        <a
          href={p.manifest}
          target="_blank"
          rel="noreferrer"
          draggable
          title="Drag into any IIIF viewer · Enter copies the Manifest URL"
          aria-label="IIIF Manifest: drag into a viewer, or press Enter to copy its address"
          onKeyDown={async (e) => {
            if (e.key !== "Enter") return;
            e.preventDefault();
            if (await copyText(p.manifest)) toast({ title: "Manifest URL copied", tone: "green" });
          }}
          className="grid size-11 shrink-0 cursor-grab place-items-center rounded-[10px] border-[1.5px] border-dashed border-border text-[11px] font-extrabold text-fg-secondary hover:border-blue hover:text-blue"
        >
          IIIF
        </a>
        <div className="flex h-10 min-w-0 flex-1 items-center gap-1.5 rounded-sm border border-border pl-3 pr-1">
          <code className="min-w-0 flex-1 truncate font-mono text-[12px]" title={p.manifest}>
            {p.manifest}
          </code>
          <Button
            variant="secondary"
            size="xs"
            icon={<Copy />}
            onClick={async () => {
              if (await copyText(p.manifest)) toast({ title: "Manifest URL copied", tone: "green" });
            }}
          >
            Copy
          </Button>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-[12px] font-semibold text-fg-muted">Open in</span>
        {p.viewers.length ? (
          p.viewers.map((v) => (
            <a
              key={v.name}
              href={v.url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex h-[30px] items-center gap-[5px] rounded-pill border border-border px-[11px] text-[12.5px] font-semibold text-fg hover:bg-surface"
            >
              {v.name}
              <ExternalLink className="size-3" />
            </a>
          ))
        ) : (
          <span className="text-[12px] text-fg-muted">
            {admin ? "No viewers set up yet (iiif.viewers in the server’s archive.yaml)." : "No viewers set up yet."}
          </span>
        )}
      </div>

      <SegmentedChoice
        label="IIIF version"
        className="flex w-full"
        value="3"
        onChange={() => undefined}
        options={[
          { value: "3", label: "Presentation 3.0" },
          {
            value: "4",
            label: "4.0 RC · Timeline",
            disabled: true,
            reason: "Not available yet: the server publishes Presentation 3.0",
          },
        ]}
      />

      <div className="flex flex-col gap-2">
        <b className="text-[12px] font-bold text-fg-secondary">What’s included</b>
        <ul className="flex flex-col gap-2">
          {included.map((i) => (
            <li key={i.key} className="flex items-center gap-2.5 text-[13px] font-medium leading-tight">
              <span
                aria-hidden
                className={cn(
                  "grid size-[18px] shrink-0 place-items-center rounded-xs border-2",
                  i.on ? "border-blue bg-blue text-white" : "border-fg-muted",
                )}
              >
                {i.on && <Check className="size-3" strokeWidth={3} />}
              </span>
              <span className={cn("flex-1", !i.on && "text-fg-secondary")}>
                {i.label}
                <span className="sr-only">{i.on ? ", included" : ", not included"}</span>
              </span>
              {i.locked && (
                <span className="inline-flex items-center gap-1 text-[11px] text-gold-dark">
                  <Lock className="size-3" /> sign-in
                </span>
              )}
              <span className="text-right text-[11.5px] text-fg-muted">{i.detail}</span>
            </li>
          ))}
        </ul>
        <p className="text-[11.5px] leading-[1.4] text-fg-muted">
          Layers are chosen for the whole archive in Settings → IIIF &amp; metadata; annotations are published when the
          transcript is open.
        </p>
      </div>

      <div className="flex flex-col gap-1.5">
        <b className="text-[12px] font-bold text-fg-secondary">Access</b>
        <ChoiceCards
          label="Access"
          size="sm"
          columns={2}
          value={p.access}
          onChange={(v) => v !== p.access && setAccess.mutate(v)}
          disabled={!canPublish || setAccess.isPending}
          disabledReason={!canPublish ? needRole("owner", ns) : undefined}
          options={ACCESS.map((a) => ({
            value: a.value,
            label: a.label,
            hint: a.hint,
            disabled: a.value !== "private" && p.access === "private" && metaProblems.length > 0,
            reason: `Fix ${metaProblems.length} problem${metaProblems.length === 1 ? "" : "s"} in the metadata first`,
          }))}
        />
        <p className="text-[12px] leading-[1.4] text-fg-secondary">{current.anon}</p>
      </div>

      <Validation
        checked={p.validation.checked}
        schema={schema}
        meta={metaProblems}
        recordingId={recordingId}
        canEdit={can("editor", ns)}
      />

      <ShareMoment recordingId={recordingId} />

      <div className="overflow-hidden rounded-[10px] border border-border">
        <div className="flex items-center gap-2 bg-surface px-3 py-2 text-[12px] font-semibold">
          <button
            type="button"
            aria-expanded={jsonOpen}
            onClick={() => setJsonOpen((o) => !o)}
            className="flex flex-1 items-center gap-2 text-left"
          >
            {jsonOpen ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
            manifest.json
          </button>
          <button
            type="button"
            className="text-blue hover:underline"
            onClick={async () => {
              if (await copyText(JSON.stringify(p.json, null, 2)))
                toast({ title: "Manifest JSON copied", tone: "green" });
            }}
          >
            Copy
          </button>
        </div>
        {jsonOpen && (
          <pre className="max-h-[360px] overflow-auto bg-background px-3 py-2.5 font-mono text-[11px] leading-[1.55] text-fg-strong">
            {JSON.stringify(p.json, null, 2)}
          </pre>
        )}
      </div>
    </div>
  );
}

/** Valid ✓ · warnings ◆ · errors ✕, each explained, with a link to the field that fixes it. */
function Validation({
  checked,
  schema,
  meta,
  recordingId,
  canEdit,
}: {
  checked: boolean;
  schema: { where: string; what: string }[];
  meta: Problem[];
  recordingId: number;
  canEdit: boolean;
}) {
  const total = schema.length + meta.length;
  const tone = total ? "border-red-border bg-red-surface" : "border-green-border bg-green-surface";
  return (
    <div className={cn("flex flex-col gap-1.5 rounded-md border p-3", tone)} role="status">
      <div className="flex items-center gap-2">
        <span aria-hidden className={cn("font-extrabold", total ? "text-red" : "text-green-dark")}>
          {total ? "✕" : "✓"}
        </span>
        <b className="flex-1 text-[13px] font-bold">
          {total ? `${total} problem${total === 1 ? "" : "s"} to fix` : checked ? "Valid" : "No metadata problems"}
        </b>
        <span className="text-[11.5px] font-medium text-fg-muted">
          {checked ? "checked against the Presentation 3 schema" : "schema check unavailable"}
        </span>
      </div>
      {!checked && (
        <span className="text-[12px] leading-[1.45] text-fg-secondary">
          The server can’t check Manifests against the IIIF schema (jsonschema isn’t installed). Metadata rules are
          still checked.
        </span>
      )}
      {meta.map((m, i) => (
        <div key={`m${i}`} className="grid grid-cols-[minmax(0,1fr)_auto] gap-2 text-[12.5px] leading-[1.45]">
          <span>
            <b className="font-semibold">{m.field ? (FIELD_LABEL[m.field as Field] ?? m.field) : "Metadata"}</b>:{" "}
            {m.message}
          </span>
          {canEdit && m.field && (
            <Link
              href={`/iiif/metadata/${recordingId}#${FIELD_ANCHOR[m.field as Field] ?? ""}`}
              className="font-semibold text-blue hover:underline"
            >
              Fix
            </Link>
          )}
        </div>
      ))}
      {schema.map((s, i) => (
        <div key={`s${i}`} className="text-[12.5px] leading-[1.45]">
          <b className="font-semibold">The Manifest doesn’t match the IIIF schema</b>
          {s.where && <code className="ml-1 font-mono text-[11px] text-fg-secondary">{s.where}</code>}
          <span className="block text-fg-secondary">{s.what}</span>
        </div>
      ))}
    </div>
  );
}

/** Share a moment as a IIIF Content State link that compatible viewers open at that time range (IP2). */
export function ShareMoment({ recordingId, initial }: { recordingId: number; initial?: { t0: number; t1?: number } }) {
  const client = useApiClient();
  const toast = useToast();
  const [from, setFrom] = useState(initial ? momentLabel(initial.t0).split("–")[0] : "0:00");
  const [to, setTo] = useState(initial?.t1 != null ? momentLabel(initial.t1) : "");
  const [copied, setCopied] = useState<{
    link: string;
    label: string;
    viewers: { name: string; url: string }[];
  } | null>(null);
  const t0 = parseClock(from);
  const t1 = to.trim() ? parseClock(to) : undefined;
  const error =
    t0 == null
      ? "Use a time like 14:29"
      : t1 === null
        ? "Use a time like 14:35"
        : t1 != null && t1 <= t0
          ? "The end comes after the start"
          : null;
  const make = useMutation({
    mutationFn: () =>
      data(
        Iiif.getContentState({
          client,
          path: { rid: recordingId },
          query: { t0: t0 ?? 0, t1: t1 ?? undefined },
        }),
      ),
    onSuccess: async (r) => {
      const link = `${window.location.origin}/iiif?iiif-content=${r.encoded}`;
      const label = momentLabel(t0 ?? 0, t1);
      setCopied({ link, label, viewers: r.viewers });
      const ok = await copyText(link);
      toast({
        title: ok ? `IIIF link to ${label} copied` : `IIIF link to ${label} ready`,
        body: "Opens at this time range in any viewer that supports content state",
        tone: "green",
      });
    },
    onError: (e) => toast({ title: "Couldn’t make the link", body: e.message, tone: "red" }),
  });
  return (
    <div className="flex flex-col gap-2">
      <b className="text-[12px] font-bold text-fg-secondary">Share a moment</b>
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex flex-col gap-1 text-[12px] font-semibold text-fg-strong">
          From
          <Input
            value={from}
            onChange={(e) => setFrom(e.target.value)}
            className="h-8 w-[84px] text-[13px]"
            mono
            invalid={t0 == null}
          />
        </label>
        <label className="flex flex-col gap-1 text-[12px] font-semibold text-fg-strong">
          To <span className="sr-only">(optional)</span>
          <Input
            value={to}
            onChange={(e) => setTo(e.target.value)}
            placeholder="optional"
            className="h-8 w-[84px] text-[13px]"
            mono
            invalid={t1 === null}
          />
        </label>
        <Button
          size="sm"
          icon={<Share2 />}
          disabled={Boolean(error) || make.isPending}
          disabledReason={error ?? undefined}
          onClick={() => make.mutate()}
        >
          Copy IIIF link to this moment
        </Button>
      </div>
      {error && (from || to) && <span className="text-[12px] text-red-dark">{error}</span>}
      {copied && (
        <div className="flex flex-col gap-1.5 rounded-md border border-border p-3">
          <b className="text-[12.5px] font-bold">What was copied</b>
          <code className="break-all font-mono text-[11.5px] leading-[1.55] text-fg-secondary">{copied.link}</code>
          <span className="text-[12px] leading-[1.4] text-fg-muted">
            Encoded content state: the Manifest, its canvas and a time fragment ({copied.label}). It opens here for
            people with access, and in any compatible viewer.
          </span>
          {copied.viewers.length > 0 && (
            <span className="flex flex-wrap gap-1.5">
              {copied.viewers.map((v) => (
                <a
                  key={v.name}
                  href={v.url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex h-7 items-center gap-1 rounded-pill border border-border px-2.5 text-[12px] font-semibold hover:bg-surface"
                >
                  Open in {v.name} <ExternalLink className="size-3" />
                </a>
              ))}
            </span>
          )}
        </div>
      )}
      {make.isError && <Banner tone="error">{make.error.message}</Banner>}
    </div>
  );
}
