"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { Admin, Resources } from "@/app/openapi-client";
import { useCreateShare, type CreatedLink } from "@/components/sharing/share-links";
import {
  SIZES,
  embedUrl,
  fmtDay,
  formatStart,
  iframeSnippet,
  normalizeOrigin,
  originAllowed,
  parseStart,
  withStart,
  type Size,
} from "@/components/sharing/embed-model";
import { Button } from "@/components/ui/button";
import { Field, Input, Select } from "@/components/ui/field";
import { CodeBlock } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { Tooltip } from "@/components/ui/tooltip";
import { data, useApiClient } from "@/lib/api/browser";
import { useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

const LAYOUTS = [
  { value: "full", name: "Full", detail: "audio + transcript + chapters" },
  { value: "compact", name: "Compact", detail: "audio + current line" },
  { value: "transcript", name: "Transcript", detail: "no audio" },
];

/** The preview iframe, scaled down to fit when the chosen width is wider than the space. */
function Preview({ src, size, title }: { src: string | null; size: Size; title: string }) {
  const box = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1);
  const { width, height } = SIZES[size];
  useEffect(() => {
    const el = box.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([e]) => setScale(Math.min(1, e.contentRect.width / width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, [width]);
  return (
    <div ref={box} className="w-full" style={{ height: height * scale }}>
      {src ? (
        <iframe
          key={src}
          src={src}
          title={`Lens player: ${title} (preview)`}
          width={width}
          height={height}
          className="origin-top-left rounded-md border border-border bg-background"
          style={{ transform: scale < 1 ? `scale(${scale})` : undefined }}
        />
      ) : (
        <div className="grid h-full place-items-center rounded-md border border-dashed border-border text-[13px] text-fg-muted">
          Loading the player…
        </div>
      )}
    </div>
  );
}

/** SH3: layout, start, where it goes (checked against the allowed embed origins), a live preview and the snippet. */
export function EmbedBuilder({
  recordingId,
  title,
  canShare,
  whyNot,
  created,
  onCreated,
  startSec,
}: {
  recordingId: number;
  title: string;
  canShare: boolean;
  whyNot: string;
  created: CreatedLink | null;
  onCreated: (l: CreatedLink) => void;
  startSec?: number;
}) {
  const client = useApiClient();
  const toast = useToast();
  const { admin } = useArchive();
  const [size, setSize] = useState<Size>(720);
  const [start, setStart] = useState(startSec ? formatStart(startSec) : "");
  const [site, setSite] = useState("");
  const create = useCreateShare(recordingId, (l) => {
    onCreated(l);
    toast({
      tone: "green",
      title: "New share link for this site",
      body: `Expires ${fmtDay(l.expires)}; revoke it with the others.`,
    });
  });
  const startS = parseStart(start);
  const origin = typeof window === "undefined" ? "" : window.location.origin;

  // Without a share token yet, the preview uses a signed link (it expires; fine for a preview).
  const signed = useQuery({
    queryKey: ["embed-link", recordingId],
    queryFn: () => data(Resources.getEmbedLink({ client, path: { rid: recordingId } })),
    enabled: !created,
    staleTime: 10 * 60_000,
  });
  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: () => data(Admin.getSettings({ client })),
    enabled: admin,
    staleTime: 5 * 60_000,
  });
  const ancestors = ((settings.data as Record<string, { values?: Record<string, unknown> }> | undefined)?.server?.values
    ?.embed_frame_ancestors ?? null) as string[] | null;

  const siteOrigin = normalizeOrigin(site);
  const siteError = site.trim() && !siteOrigin ? "Enter a site address, e.g. https://blog.example.com" : null;
  const blocked = Boolean(siteOrigin && ancestors && !originAllowed(siteOrigin, ancestors, origin));
  const src = created ? embedUrl(origin, recordingId, { token: created.token, start: startS }) : null;
  const previewSrc = src ?? (signed.data?.url ? withStart(signed.data.url, startS) : null);
  const snippet = src ? iframeSnippet({ src, size, title }) : null;
  const copyReason = !created
    ? "Create a share link for this site first"
    : startS == null
      ? "Fix the start time"
      : !siteOrigin
        ? "Enter where it will be embedded"
        : blocked
          ? "This site isn’t an allowed embed origin"
          : undefined;

  const copy = async () => {
    if (!snippet) return;
    try {
      await navigator.clipboard.writeText(snippet);
      toast({
        tone: "green",
        title: "Snippet copied",
        body: siteOrigin ? `Paste it into a page on ${siteOrigin}.` : undefined,
      });
    } catch {
      toast({
        tone: "red",
        title: "Couldn’t copy",
        body: "Select the snippet below and copy it.",
      });
    }
  };

  return (
    <div className="grid gap-5 lg:grid-cols-[360px_minmax(0,1fr)]">
      <div className="flex flex-col gap-3.5">
        <div className="flex flex-col gap-1.5">
          <span className="text-[13px] font-bold text-fg-strong">Recording</span>
          <span className="truncate rounded-sm border border-border bg-surface px-3.5 py-2 text-[14px] text-fg">
            {title}
          </span>
        </div>
        <div className="flex flex-col gap-1.5">
          <span id="embed-layout" className="text-[13px] font-bold text-fg-strong">
            Layout
          </span>
          <div role="radiogroup" aria-labelledby="embed-layout" className="grid grid-cols-3 gap-1.5">
            {LAYOUTS.map((l, i) => {
              const on = i === 0;
              const card = (
                <span
                  role="radio"
                  aria-checked={on}
                  aria-disabled={!on || undefined}
                  tabIndex={on ? 0 : -1}
                  className={cn(
                    "flex flex-col gap-[3px] rounded-[10px] p-2.5",
                    on
                      ? "border-2 border-blue bg-blue-surface"
                      : "cursor-not-allowed border border-border bg-background opacity-60",
                  )}
                >
                  <b className="text-[13px] font-bold leading-tight text-fg">{l.name}</b>
                  <span className="text-[11.5px] leading-snug text-fg-secondary">{l.detail}</span>
                </span>
              );
              return on ? (
                <span key={l.value}>{card}</span>
              ) : (
                <Tooltip
                  key={l.value}
                  content="Not available yet: the player has one layout (recordings without audio show the transcript only)"
                >
                  {card}
                </Tooltip>
              );
            })}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-2.5">
          <Field
            label="Start at"
            hint="m:ss, or empty for the start"
            error={startS == null ? "Use m:ss, e.g. 14:02" : undefined}
          >
            {({ id, describedBy, invalid }) => (
              <Input
                id={id}
                aria-describedby={describedBy}
                invalid={invalid}
                value={start}
                placeholder="0:00"
                onChange={(e) => setStart(e.target.value)}
              />
            )}
          </Field>
          <Field label="Theme" hint="Follows the visitor’s system">
            {({ id, describedBy }) => (
              <Tooltip content="Not available yet: the player follows the visitor’s light or dark setting">
                <span tabIndex={0}>
                  <Select
                    id={id}
                    aria-describedby={describedBy}
                    disabled
                    value="auto"
                    options={[{ value: "auto", label: "Match site (auto)" }]}
                  />
                </span>
              </Tooltip>
            )}
          </Field>
        </div>
        <Field
          label="Where will it be embedded?"
          error={
            siteError ??
            (blocked ? "Not an allowed embed origin. An admin can add it in Settings → Access & embedding." : undefined)
          }
          hint={
            siteOrigin && ancestors && !blocked
              ? `✓ ${siteOrigin} may embed the player`
              : !admin
                ? "Sites can show the player only if an admin has allowed their address (Settings → Access & embedding)."
                : undefined
          }
        >
          {({ id, describedBy, invalid }) => (
            <Input
              id={id}
              aria-describedby={describedBy}
              invalid={invalid}
              mono
              placeholder="https://blog.example.com"
              value={site}
              onChange={(e) => setSite(e.target.value)}
            />
          )}
        </Field>
        <div className="flex gap-2.5 rounded-[10px] border border-gold-border bg-gold-surface px-3 py-2.5 text-[12.5px] leading-[1.45] text-fg-strong">
          <span aria-hidden className="mt-[5px] size-2 shrink-0 rotate-45 bg-gold" />
          <span>
            <b className="text-fg">Outside sites need a share link.</b> Visitors there aren’t signed in, so the snippet
            carries a link token
            {created ? ` (expires ${fmtDay(created.expires)})` : ""}. Inside the archive, people use their own session.
          </span>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="primary" disabled={Boolean(copyReason)} disabledReason={copyReason} onClick={copy}>
            Copy snippet
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={!canShare || create.isPending}
            disabledReason={whyNot}
            onClick={() => create.mutate(30)}
          >
            {created ? "Use a new link" : "Create a share link"}
          </Button>
        </div>
        {snippet && (
          <CodeBlock
            text={snippet}
            label="the embed snippet"
            className="[&_pre]:whitespace-pre-wrap [&_pre]:break-all"
          />
        )}
      </div>

      <div className="flex min-w-0 flex-col gap-3 rounded-md bg-surface p-4">
        <div className="flex items-center gap-2 text-[12.5px] font-semibold text-fg-secondary">
          <span className="flex-1">Live preview</span>
          <div role="radiogroup" aria-label="Width" className="flex gap-1.5">
            {([360, 720] as Size[]).map((w) => (
              <button
                key={w}
                type="button"
                role="radio"
                aria-checked={size === w}
                onClick={() => setSize(w)}
                className={cn(
                  "rounded-pill border px-2.5 py-1 text-[12.5px] font-semibold",
                  size === w
                    ? "border-blue-border bg-blue-surface text-fg-accent"
                    : "border-border bg-background text-fg-secondary hover:text-fg",
                )}
              >
                {w}
              </button>
            ))}
          </div>
        </div>
        <Preview src={previewSrc} size={size} title={title} />
        <div className="flex justify-between text-[11.5px] font-medium text-fg-muted">
          <span>
            {created ? "The real player, with this link’s token" : "The real player (preview link; it expires)"}
          </span>
          <span>{created ? `Link expires ${fmtDay(created.expires)}` : ""}</span>
        </div>
      </div>
    </div>
  );
}
