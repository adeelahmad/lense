"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Resources } from "@/app/openapi-client";
import type { Share } from "@/app/openapi-client/types.gen";
import {
  MAX_DAYS,
  embedUrl,
  embeddedLine,
  expiryDate,
  fmtDay,
  iframeSnippet,
  linkState,
  playsLine,
} from "@/components/sharing/embed-model";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import { CodeBlock } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { plural } from "@/lib/format";
import { cn } from "@/lib/utils";

export type CreatedLink = { id: string; token: string; embed: string; short?: string | null; expires: Date };

/** Share links of one recording (editors can list, create and revoke them). */
export function useShares(recordingId: number, enabled: boolean) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["shares", recordingId],
    queryFn: () => data(Resources.listShares({ client, path: { rid: recordingId } })),
    enabled,
    staleTime: 0, // plays and embedding sites change while the dialog is closed
  });
}

export function useCreateShare(recordingId: number, onCreated: (l: CreatedLink) => void) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: async (days: number) => ({
      days,
      r: await data(
        Resources.createShare({
          client,
          path: { rid: recordingId },
          body: { days },
        }),
      ),
    }),
    onSuccess: ({ days, r }) => {
      void qc.invalidateQueries({ queryKey: ["shares", recordingId] });
      onCreated({ id: r.id, token: r.token, embed: r.embed, short: r.short, expires: expiryDate(days) });
    },
    onError: (e: Error) =>
      toast({
        tone: "red",
        title: "Couldn’t create a share link",
        body: e.message,
      }),
  });
}

const STATE_LABEL = { active: "Active", expired: "Expired", revoked: "Revoked" } as const;

/** One link: whether it still works, who made it, how it's been used; editors can revoke it. */
function LinkRow({
  share: s,
  canRevoke,
  onRevoke,
  revoking,
}: {
  share: Share;
  canRevoke: boolean;
  onRevoke: () => void;
  revoking: boolean;
}) {
  const [confirm, setConfirm] = useState(false);
  const state = linkState(s);
  const sites = embeddedLine(s.embedded_on);
  return (
    <li className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-1 border-t border-border py-2.5">
      <code className={cn("truncate font-mono text-[12.5px] font-medium", s.active ? "text-fg" : "text-fg-muted")}>
        link {s.id}…
      </code>
      <span className={cn("text-[12px] font-semibold", s.active ? "text-green-dark" : "text-fg-muted")}>
        {STATE_LABEL[state]}
      </span>
      <span className="col-span-2 text-[12px] text-fg-muted">
        {[
          s.created_by,
          s.created_at ? `created ${fmtDay(s.created_at)}` : null,
          state === "revoked"
            ? `revoked ${s.revoked_at ? fmtDay(s.revoked_at) : ""}${s.revoked_by ? ` by ${s.revoked_by}` : ""}`.trim()
            : s.expires_at
              ? `${state === "expired" ? "expired" : "expires"} ${fmtDay(s.expires_at)}`
              : null,
        ]
          .filter(Boolean)
          .join(" · ")}
      </span>
      <span className="col-span-2 text-[12px] text-fg-secondary">
        {playsLine(s.plays, s.played_at)}
        {sites ? ` · ${sites}` : ""}
      </span>
      {s.active &&
        (confirm ? (
          <div className="col-span-2 flex flex-wrap items-center gap-2 pt-1">
            <span className="flex-1 text-[12.5px] text-fg-strong" role="alert">
              Revoke this link? Pages that embed it stop playing; other links keep working.
            </span>
            <Button size="sm" variant="ghost" onClick={() => setConfirm(false)}>
              Keep it
            </Button>
            <Button size="sm" variant="danger" onClick={onRevoke} disabled={revoking}>
              Revoke
            </Button>
          </div>
        ) : (
          <div className="col-span-2 pt-0.5">
            <Button
              size="sm"
              variant="danger-ghost"
              onClick={() => setConfirm(true)}
              disabled={!canRevoke}
              disabledReason="Only editors can revoke links"
              aria-label={`Revoke link ${s.id}`}
            >
              Revoke…
            </Button>
          </div>
        ))}
    </li>
  );
}

/** SH1: create a link (days), show it once (its short address) with its embed snippet, list the links with their
 * plays and the sites that embed them, revoke one or all. */
export function ShareLinks({
  recordingId,
  title,
  namespace,
  canShare,
  whyNot,
  created,
  onCreated,
  onClose,
  onCustomise,
  startSec,
}: {
  recordingId: number;
  title: string;
  namespace?: string | null;
  canShare: boolean;
  whyNot: string;
  created: CreatedLink | null;
  onCreated: (l: CreatedLink | null) => void;
  onClose: () => void;
  onCustomise: () => void;
  startSec?: number;
}) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const [days, setDays] = useState("30");
  const [confirm, setConfirm] = useState(false);
  const shares = useShares(recordingId, canShare);
  const create = useCreateShare(recordingId, (l) => onCreated(l));
  const revoke = useMutation({
    mutationFn: () => data(Resources.revokeShares({ client, path: { rid: recordingId } })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["shares", recordingId] });
      setConfirm(false);
      onCreated(null);
      toast({
        title: "All share links revoked",
        body: "Anyone opening them now sees “This link isn’t available”.",
      });
    },
    onError: (e: Error) =>
      toast({
        tone: "red",
        title: "Couldn’t revoke the links",
        body: e.message,
      }),
  });
  const revokeOne = useMutation({
    mutationFn: (sid: string) => data(Resources.revokeShare({ client, path: { rid: recordingId, sid } })),
    onSuccess: (_r, sid) => {
      void qc.invalidateQueries({ queryKey: ["shares", recordingId] });
      if (created?.id === sid) onCreated(null);
      toast({ title: "Link revoked", body: "Anyone opening it now sees “This link isn’t available”." });
    },
    onError: (e: Error) =>
      toast({
        tone: "red",
        title: "Couldn’t revoke the link",
        body: e.message,
      }),
  });
  const n = Number(days);
  const validDays = Number.isInteger(n) && n >= 1 && n <= MAX_DAYS;
  const active = (shares.data ?? []).filter((s) => s.active).length;
  const origin = typeof window === "undefined" ? "" : window.location.origin;

  const list = canShare && (shares.data?.length ?? 0) > 0 && (
    <details className="group rounded-md border border-border px-4 py-2.5">
      <summary className="cursor-pointer select-none text-[13.5px] font-bold text-fg">
        Share links <span className="font-medium text-fg-muted">· {plural(active, "active link")}</span>
      </summary>
      <div className="mt-2">
        <ul className="flex flex-col">
          {(shares.data ?? []).map((s) => (
            <LinkRow
              key={s.id}
              share={s}
              canRevoke={canShare}
              revoking={revokeOne.isPending && revokeOne.variables === s.id}
              onRevoke={() => revokeOne.mutate(s.id)}
            />
          ))}
        </ul>
        <p className="border-t border-border pt-2 text-[12px] text-fg-muted">
          A link’s address is shown only when it’s created. Plays count each time its player starts, once per visit;
          sites are the pages that embed it.
        </p>
      </div>
    </details>
  );

  const footer = canShare && (active > 0 || created) && (
    <div className="flex flex-wrap items-center gap-2.5 border-t border-border pt-3">
      {confirm ? (
        <>
          <span className="flex-1 text-[13px] text-fg-strong" role="alert">
            Revoke {active === 1 ? "the active link" : `all ${active} active links`}? Pages that embed them stop
            playing.
          </span>
          <Button size="sm" variant="ghost" onClick={() => setConfirm(false)}>
            Keep them
          </Button>
          <Button size="sm" variant="danger" onClick={() => revoke.mutate()} disabled={revoke.isPending}>
            Revoke all
          </Button>
        </>
      ) : (
        <>
          <span className="flex-1 text-[12.5px] text-fg-secondary">
            {shares.isSuccess ? `This recording has ${plural(active, "active link")}.` : ""}
          </span>
          <Button
            size="sm"
            variant="danger-ghost"
            onClick={() => setConfirm(true)}
            disabled={!active}
            disabledReason="No active links"
          >
            Revoke all links…
          </Button>
        </>
      )}
    </div>
  );

  if (created) {
    const url = `${origin}${created.short ?? created.embed}`;
    const snippet = iframeSnippet({
      src: embedUrl(origin, recordingId, {
        token: created.token,
        start: startSec,
      }),
      size: 720,
      title,
    });
    return (
      <div className="flex flex-col gap-3.5">
        <div className="flex flex-col gap-1.5">
          <span className="text-[13px] font-bold text-fg-strong">Link · expires {fmtDay(created.expires)}</span>
          <CodeBlock inline text={url} label="the share link" className="w-full justify-between [&_code]:text-[13px]" />
          <span className="text-[12px] text-fg-muted">
            Shown once: copy it now. Anyone with it can play and read this recording until it expires.
          </span>
        </div>
        <div className="flex flex-col gap-1.5">
          <span className="text-[13px] font-bold text-fg-strong">Embed snippet</span>
          <CodeBlock
            text={snippet}
            label="the embed snippet"
            className="[&_pre]:whitespace-pre-wrap [&_pre]:break-all"
          />
          <button
            type="button"
            onClick={onCustomise}
            className="self-start text-[12.5px] font-semibold text-fg-accent hover:underline"
          >
            Customise in the embed builder →
          </button>
        </div>
        {list}
        {footer}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={() => onCreated(null)}>
            Create another
          </Button>
          <Button variant="primary" onClick={onClose}>
            Done
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3.5">
      <p className="text-[14px] leading-normal text-fg-secondary">
        People in {namespace ?? "its namespace"} can already open it at{" "}
        <code className="font-mono text-[12.5px] text-fg">/resources/{recordingId}</code>. A share link lets anyone with
        it play and read this one recording — nothing else.
      </p>
      <Field
        label="Expires after (days)"
        hint={validDays ? `Expires ${fmtDay(expiryDate(n))} · maximum ${MAX_DAYS}` : undefined}
        error={days.trim() && !validDays ? `A whole number from 1 to ${MAX_DAYS}.` : undefined}
      >
        {({ id, describedBy, invalid }) => (
          <Input
            id={id}
            aria-describedby={describedBy}
            invalid={invalid}
            inputMode="numeric"
            value={days}
            disabled={!canShare}
            onChange={(e) => setDays(e.target.value)}
            onKeyDown={(e) => {
              if (e.key !== "Enter") return;
              e.preventDefault();
              if (validDays && canShare) create.mutate(n);
            }}
          />
        )}
      </Field>
      {list}
      {footer}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button
          variant="primary"
          disabled={!canShare || !validDays || create.isPending}
          disabledReason={canShare ? undefined : whyNot}
          onClick={() => create.mutate(n)}
        >
          {create.isPending ? "Creating…" : "Create share link"}
        </Button>
      </div>
    </div>
  );
}
