"use client";

import { EyeOff, Globe, Lock, Star, type LucideIcon } from "lucide-react";

import {
  ACCESS,
  PARTS,
  accessLabel,
  accessSummary,
  togglePart,
  type Access,
  type AccessValue,
} from "@/components/access/model";
import { ChoiceCards } from "@/components/settings/controls";
import { Checkbox, Switch } from "@/components/ui/field";
import { Tooltip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

export const ACCESS_ICON: Record<Access, LucideIcon> = { public: Globe, restricted: Lock, private: EyeOff };

/**
 * Who may see a recording: public, restricted or private; the parts a public one opens to everyone; featured.
 * Controlled; the Access dialog, the IIIF panel and the metadata editor each decide when to save.
 */
export function AccessFields({
  value,
  onChange,
  disabled,
  disabledReason,
  publishBlocked,
  size = "md",
}: {
  value: AccessValue;
  onChange: (v: AccessValue) => void;
  disabled?: boolean;
  disabledReason?: string;
  /** Why it can't be made public yet (e.g. metadata problems); a public one can still change its parts. */
  publishBlocked?: string;
  size?: "sm" | "md";
}) {
  const current = ACCESS.find((a) => a.value === value.access) ?? ACCESS[ACCESS.length - 1];
  const pub = value.access === "public";
  const partsReason = disabled ? disabledReason : "Only a public recording opens parts to everyone";
  return (
    <div className="flex flex-col gap-3">
      <ChoiceCards
        label="Access"
        size={size}
        columns={3}
        className="max-sm:!grid-cols-1"
        value={value.access}
        onChange={(v) => onChange({ ...value, access: v as Access })}
        disabled={disabled}
        disabledReason={disabledReason}
        options={ACCESS.map((a) => ({
          value: a.value,
          label: a.label,
          hint: a.hint,
          disabled: a.value === "public" && !pub && Boolean(publishBlocked),
          reason: publishBlocked,
        }))}
      />
      <p className="text-[12.5px] leading-[1.45] text-fg-secondary">{current.anon}</p>
      <fieldset className="flex flex-col gap-2" aria-describedby="access-open-hint">
        <legend className="mb-1.5 text-[13px] font-bold text-fg-strong">Open to everyone</legend>
        {PARTS.map((p) => {
          const box = (
            <Checkbox
              checked={pub && value.open.includes(p.value)}
              onCheckedChange={(on) => onChange({ ...value, open: togglePart(value.open, p.value, on) })}
              disabled={disabled || !pub}
              label={
                <span className="flex flex-col">
                  <span className="text-[13.5px] font-semibold text-fg">{p.label}</span>
                  <span className="text-[12px] text-fg-muted">{p.hint}</span>
                </span>
              }
            />
          );
          return disabled || !pub ? (
            <Tooltip key={p.value} content={partsReason}>
              <span className="w-fit">{box}</span>
            </Tooltip>
          ) : (
            <span key={p.value}>{box}</span>
          );
        })}
        <p id="access-open-hint" className="text-[12px] leading-[1.4] text-fg-muted">
          {pub
            ? "Closed parts stay with people who have access; signed-in people can ask for them."
            : "People with access always get everything."}
        </p>
      </fieldset>
      <Switch
        checked={pub && value.featured}
        onCheckedChange={(on) => onChange({ ...value, featured: on })}
        disabled={disabled || !pub}
        label={
          <span className="flex flex-col">
            <span className="text-[13.5px] font-semibold text-fg">Featured</span>
            <span className="text-[12px] text-fg-muted">
              {pub ? "Shown on the public home page" : "Only public recordings are featured"}
            </span>
          </span>
        }
      />
    </div>
  );
}

/** A recording's access as a small chip: an icon, the level and a star when featured. */
export function AccessBadge({
  value,
  onClick,
  compact,
  className,
}: {
  value: Partial<AccessValue> | null | undefined;
  onClick?: () => void;
  /** Icon only (the level is in the tooltip and the accessible name). */
  compact?: boolean;
  className?: string;
}) {
  const level = (value?.access ?? "private") as Access;
  const Icon = ACCESS_ICON[level];
  const summary = accessSummary(value);
  const inner = (
    <>
      <Icon aria-hidden className="size-3.5 shrink-0" />
      {!compact && <span>{accessLabel(level)}</span>}
      {level === "public" && value?.featured && <Star aria-hidden className="size-3 shrink-0 fill-current" />}
    </>
  );
  const cls = cn(
    "inline-flex shrink-0 items-center gap-1 whitespace-nowrap text-fg-secondary",
    !compact && "h-[22px] rounded-pill border border-border px-2 text-[12px] font-semibold",
    onClick && "hover:bg-surface hover:text-fg",
    className,
  );
  return (
    <Tooltip content={summary}>
      {onClick ? (
        <button type="button" onClick={onClick} aria-label={`Access: ${summary}`} className={cls}>
          {inner}
        </button>
      ) : (
        <span role="img" aria-label={`Access: ${summary}`} tabIndex={compact ? 0 : undefined} className={cls}>
          {inner}
        </span>
      )}
    </Tooltip>
  );
}
