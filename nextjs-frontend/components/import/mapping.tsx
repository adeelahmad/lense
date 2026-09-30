"use client";

import { useId, useMemo } from "react";

import type { ImportPreview, SpeakerDirectory } from "@/app/openapi-client/types.gen";
import { GENERIC_LABEL, isUntimed, mappedNames, parseMapping } from "@/components/import/files";
import { speakerColor } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/** Colour per label, by first appearance in the preview (then the rest). */
export function labelColors(preview: ImportPreview | undefined): Map<string, string> {
  const order: string[] = [];
  for (const l of preview?.preview ?? []) if (l.speaker && !order.includes(l.speaker)) order.push(l.speaker);
  for (const s of preview?.speakers ?? []) if (!order.includes(s)) order.push(s);
  return new Map(order.map((s, i) => [s, speakerColor(i)]));
}

/** "label = speaker", one per line, with what each name will become in the namespace. */
export function MappingField({
  value,
  onChange,
  preview,
  namespace,
  directory,
  large,
}: {
  value: string;
  onChange: (v: string) => void;
  preview: ImportPreview | undefined;
  namespace: string | null;
  directory: SpeakerDirectory | undefined;
  large?: boolean;
}) {
  const id = useId();
  const labels = preview?.speakers ?? [];
  const m = useMemo(() => parseMapping(value, labels), [value, labels]);
  const names = mappedNames(labels, m);
  const colors = labelColors(preview);
  const known = useMemo(() => {
    const s = new Set<string>();
    for (const sp of directory?.speakers ?? []) {
      if (sp.name) s.add(sp.name);
      s.add(sp.label);
    }
    return s;
  }, [directory]);
  const targets = [...new Set([...names.values()])];
  const real = targets.filter((n) => !GENERIC_LABEL.test(n));
  const existing = real.filter((n) => known.has(n));
  const fresh = real.filter((n) => !known.has(n));
  const generic = targets.filter((n) => GENERIC_LABEL.test(n));

  if (!labels.length) {
    return (
      <p className="text-[12.5px] text-fg-muted">
        No speaker labels found, so there’s nothing to map. Lines without a “Name:” label are imported without a
        speaker.
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-baseline gap-2">
        <label htmlFor={id} className="text-[13px] font-bold text-fg">
          Speaker mapping
        </label>
        <span className="text-[12px] text-fg-muted">
          one per line · <code className="font-mono">label = speaker</code>
        </span>
      </div>
      <textarea
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        rows={Math.min(8, Math.max(2, labels.length))}
        spellCheck={false}
        aria-invalid={m.errors.length > 0 || undefined}
        aria-describedby={`${id}-notes`}
        className={cn(
          "w-full resize-y rounded-sm border border-border bg-background px-3.5 py-2.5 font-mono leading-[1.7] text-fg outline-none focus:border-blue focus:shadow-[0_0_0_3px_var(--intent-surface)] aria-[invalid=true]:border-red",
          large ? "text-[15px]" : "text-[13.5px]",
        )}
      />
      <div id={`${id}-notes`} className="flex flex-col gap-1 text-[12px] leading-snug" aria-live="polite">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-fg-secondary">
          {labels.map((l) => (
            <span key={l} className="inline-flex items-center gap-1">
              <span aria-hidden className="size-2.5 rounded-full" style={{ background: colors.get(l) }} />
              {names.get(l)}
            </span>
          ))}
          {namespace && existing.length > 0 && (
            <span>
              {existing.length === real.length && real.length > 1 ? "all" : existing.join(", ")}{" "}
              {existing.length === 1 ? "exists" : "exist"} in {namespace} ✓
            </span>
          )}
          <span className="text-fg-muted">· names that don’t exist become new speakers</span>
        </div>
        {namespace &&
          fresh.map((n) => (
            <span key={n} className="text-gold-dark">
              ◆ “{n}” isn’t a speaker in {namespace} yet — it will be created.
            </span>
          ))}
        {generic.length > 0 && (
          <span className="text-fg-muted">
            {generic.join(", ")} will stay unnamed — you can name {generic.length > 1 ? "them" : "it"} after importing.
          </span>
        )}
        {m.errors.map((e) => (
          <span key={e} className="text-red-dark" role="alert">
            {e}
          </span>
        ))}
        {m.warnings.map((w) => (
          <span key={w} className="text-gold-dark">
            ◆ {w}
          </span>
        ))}
      </div>
    </div>
  );
}

/** The parser's first lines: time, speaker, text (serif, like the transcript). Untimed formats show "~" estimates. */
export function PreviewLines({
  preview,
  mapping,
  variant = "table",
  max = 6,
}: {
  preview: ImportPreview;
  mapping?: string;
  variant?: "table" | "plain";
  max?: number;
}) {
  const colors = labelColors(preview);
  const names = mapping != null ? mappedNames(preview.speakers, parseMapping(mapping, preview.speakers)) : null;
  const est = isUntimed(preview.format);
  const lines = preview.preview.slice(0, max);
  if (variant === "plain") {
    return (
      <ol className="flex flex-col gap-2.5" aria-label="First lines as imported">
        {lines.map((l, i) => (
          <li
            key={i}
            className="grid grid-cols-[44px_minmax(0,1fr)] items-baseline gap-x-2.5 gap-y-0.5 sm:grid-cols-[52px_84px_minmax(0,1fr)]"
          >
            <span className={cn("tabular text-[12px] text-fg-muted", est && "italic")}>
              {est ? "~" : ""}
              {l.time}
            </span>
            <span
              className="truncate text-[12px] font-semibold"
              style={{
                color: l.speaker ? colors.get(l.speaker) : "var(--text-secondary)",
              }}
            >
              {l.speaker ? (names?.get(l.speaker) ?? l.speaker) : "—"}
            </span>
            <span className="col-span-2 font-serif text-[14.5px] leading-[1.45] text-fg sm:col-span-1">{l.text}</span>
          </li>
        ))}
      </ol>
    );
  }
  return (
    <div className="overflow-hidden rounded-md border border-border">
      <div className="border-b border-border bg-surface px-3 py-2 text-[12px] font-semibold text-fg-secondary">
        First segments
      </div>
      <ol aria-label="First segments">
        {lines.map((l, i) => (
          <li
            key={i}
            className="grid grid-cols-[48px_minmax(0,1fr)] items-baseline gap-x-2.5 gap-y-0.5 border-b border-border px-3 py-2 last:border-b-0 sm:grid-cols-[64px_96px_minmax(0,1fr)]"
          >
            <span className={cn("tabular text-[12px] font-medium text-fg-muted", est && "italic")}>
              {est ? "~" : ""}
              {l.time}
            </span>
            <span
              className="truncate font-mono text-[12px] font-semibold"
              style={{
                color: l.speaker ? colors.get(l.speaker) : "var(--text-muted)",
              }}
            >
              {l.speaker ?? "—"}
            </span>
            <span className="col-span-2 font-serif text-[14.5px] leading-[1.45] text-fg sm:col-span-1">{l.text}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}
