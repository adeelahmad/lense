"use client";

import { KeyRound, Lock, LockOpen } from "lucide-react";
import { useId, useState, type ReactNode } from "react";

import { ChoiceCards, Pills } from "@/components/settings/controls";
import { fieldId, type FieldSpec } from "@/components/settings/model";
import { Button } from "@/components/ui/button";
import { Checkbox, Input, Select, Switch, Textarea } from "@/components/ui/field";
import { absolute } from "@/lib/format";
import { cn } from "@/lib/utils";

export type FieldState = {
  value: unknown;
  error?: string | null;
  locked?: string | null;
  onChange: (v: unknown) => void;
};

function Message({ id, error, hint }: { id: string; error?: string | null; hint?: ReactNode }) {
  if (!error && !hint) return null;
  return (
    <p
      id={id}
      role={error ? "alert" : undefined}
      className={cn("text-[12.5px] leading-snug", error ? "text-red-dark" : "text-fg-muted")}
    >
      {error || hint}
    </p>
  );
}

/** A labelled control for one setting, by kind. Env-locked settings are read-only with the variable named. */
export function SettingField({
  spec,
  state,
  hint,
  className,
  label,
}: {
  spec: FieldSpec;
  state: FieldState;
  hint?: ReactNode;
  className?: string;
  label?: ReactNode;
}) {
  const auto = useId();
  const id = `${fieldId(spec).replace(/\./g, "-")}-${auto}`;
  const msg = `${id}-msg`;
  const { value, error, locked, onChange } = state;
  const described = error || hint || spec.hint || locked ? msg : undefined;
  const text = locked ? `Set by the environment (${locked}); change it there and restart.` : (hint ?? spec.hint);
  const disabled = Boolean(locked);
  const title = label ?? spec.label;

  if (spec.kind === "switch")
    return (
      <div className={cn("flex items-start justify-between gap-5", className)}>
        <div className="flex flex-col gap-1">
          <span className="text-[14px] font-bold leading-tight text-fg">{title}</span>
          {text && (
            <span id={msg} className="text-[13px] leading-[1.45] text-fg-secondary">
              {text}
            </span>
          )}
        </div>
        <span className="shrink-0 pt-0.5" aria-describedby={text ? msg : undefined}>
          <Switch aria-label={spec.label} checked={Boolean(value)} disabled={disabled} onCheckedChange={onChange} />
        </span>
      </div>
    );

  return (
    <div className={cn("flex min-w-0 flex-col gap-1.5", className)}>
      {spec.kind === "pills" || spec.kind === "cards" || spec.kind === "checks" ? (
        <span id={`${id}-label`} className="text-[13px] font-bold leading-tight text-fg-strong">
          {title}
        </span>
      ) : (
        <label htmlFor={id} className="text-[13px] font-bold leading-tight text-fg-strong">
          {title}
        </label>
      )}
      {spec.kind === "select" ? (
        <Select
          id={id}
          aria-describedby={described}
          invalid={Boolean(error)}
          value={String(value ?? "")}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
          options={spec.options ?? []}
        />
      ) : spec.kind === "pills" ? (
        <Pills
          label={String(spec.label)}
          options={spec.options ?? []}
          value={String(value ?? "")}
          onChange={(v) => !disabled && onChange(v)}
        />
      ) : spec.kind === "cards" ? (
        <ChoiceCards
          label={String(spec.label)}
          options={spec.options ?? []}
          value={String(value ?? "")}
          onChange={onChange}
          disabled={disabled}
          columns={Math.min(spec.options?.length ?? 3, 3)}
          size="sm"
          className="max-sm:!grid-cols-1"
        />
      ) : spec.kind === "lines" ? (
        <Textarea
          id={id}
          aria-describedby={described}
          invalid={Boolean(error)}
          mono={spec.mono}
          rows={4}
          value={String(value ?? "")}
          readOnly={disabled}
          spellCheck={false}
          onChange={(e) => onChange(e.target.value)}
          className="leading-[1.7]"
        />
      ) : spec.kind === "checks" ? (
        <div role="group" aria-labelledby={`${id}-label`} className="flex flex-wrap gap-x-3 gap-y-2">
          {(spec.options ?? []).map((o) => {
            const list = (value as string[]) ?? [];
            return (
              <Checkbox
                key={o.value}
                label={o.label}
                disabled={disabled}
                checked={list.includes(o.value)}
                onCheckedChange={(on) => onChange(on ? [...list, o.value] : list.filter((x) => x !== o.value))}
              />
            );
          })}
        </div>
      ) : (
        <Input
          id={id}
          aria-describedby={described}
          invalid={Boolean(error)}
          mono={spec.mono}
          inputMode={spec.kind === "int" || spec.kind === "number" || spec.kind === "days" ? "decimal" : undefined}
          placeholder={spec.placeholder}
          value={String(value ?? "")}
          readOnly={disabled}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
      <Message id={msg} error={error} hint={text} />
    </div>
  );
}

/**
 * A write-only secret, in its three states (Settings ST3): not set · set ("updated by … on …", Clear, Replace) ·
 * replacing (Cancel). `value` undefined = unchanged, "" = clear, text = replace.
 */
export function SecretSetting({
  label,
  isSet,
  updatedBy,
  updatedAt,
  value,
  onChange,
  placeholder = "Paste an API key",
}: {
  label: string;
  isSet: boolean;
  updatedBy?: string | null;
  updatedAt?: string | null;
  value: string | undefined;
  onChange: (v: string | undefined) => void;
  placeholder?: string;
}) {
  const id = useId();
  const [replacing, setReplacing] = useState(false);
  const box = "flex h-10 items-center gap-1.5 rounded-sm border pl-3 pr-1";
  const showSet = isSet && !replacing && value === undefined;
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-[13px] font-bold leading-tight text-fg-strong">
        {label}
      </label>
      {value === "" ? (
        <div className={cn(box, "border-red-border bg-red-surface")}>
          <LockOpen aria-hidden className="size-3.5 shrink-0 text-red-dark" />
          <span id={id} className="flex-1 text-[13px] font-medium text-red-dark">
            Will be cleared when you save
          </span>
          <Button variant="ghost" size="xs" onClick={() => onChange(undefined)}>
            Undo
          </Button>
        </div>
      ) : showSet ? (
        <div className={cn(box, "border-border bg-surface")}>
          <Lock aria-hidden className="size-3.5 shrink-0 text-green-dark" />
          <span id={id} className="flex-1 truncate text-[13px] font-medium">
            Set{updatedBy ? ` · updated by ${updatedBy}` : ""}
            {updatedAt ? ` on ${absolute(updatedAt)}` : ""}
          </span>
          <Button variant="danger-ghost" size="xs" onClick={() => onChange("")}>
            Clear
          </Button>
          <Button variant="secondary" size="xs" onClick={() => setReplacing(true)}>
            Replace
          </Button>
        </div>
      ) : (
        <div className={cn(box, isSet ? "border-blue shadow-[0_0_0_3px_var(--intent-surface)]" : "border-border")}>
          {isSet ? (
            <KeyRound aria-hidden className="size-3.5 shrink-0 text-blue" />
          ) : (
            <LockOpen aria-hidden className="size-3.5 shrink-0 text-fg-muted" />
          )}
          <input
            id={id}
            type="password"
            autoComplete="off"
            autoFocus={replacing}
            value={value ?? ""}
            placeholder={isSet ? "Paste the new key" : placeholder}
            onChange={(e) => onChange(e.target.value || undefined)}
            className="min-w-0 flex-1 bg-transparent font-mono text-[13px] outline-none placeholder:font-sans placeholder:text-fg-muted"
          />
          {isSet && (
            <Button
              variant="secondary"
              size="xs"
              onClick={() => {
                setReplacing(false);
                onChange(undefined);
              }}
            >
              Cancel
            </Button>
          )}
        </div>
      )}
      <p className="text-[12px] leading-[1.3] text-fg-muted">
        {showSet
          ? "The value is never shown again, not even to admins."
          : isSet && value !== ""
            ? "The old key keeps working until you save."
            : "Stored encrypted with the server’s key."}
      </p>
    </div>
  );
}

/**
 * Three zones from 0 to 1 (Voice IDs, faces): below review = new · between = ◆ review queue · at match and above =
 * ✓ auto-match. When the thresholds cross, the markers show it in red.
 */
export function ZoneBar({
  match,
  review,
  low = "New speaker",
}: {
  match: number | null;
  review: number | null;
  low?: string;
}) {
  const m = typeof match === "number" && match >= 0 && match <= 1 ? match : null;
  const r = typeof review === "number" && review >= 0 && review <= 1 ? review : null;
  if (m == null || r == null)
    return (
      <div className="grid h-11 place-items-center rounded-sm border border-dashed border-border text-[12px] text-fg-muted">
        Enter both thresholds to see the zones
      </div>
    );
  const valid = r <= m;
  const lowEnd = valid ? r : m;
  const pctOf = (x: number) => `${(x * 100).toFixed(1)}%`;
  const label = valid
    ? `Three zones from 0 to 1: ${low.toLowerCase()} below ${r}, review between ${r} and ${m}, auto-match at ${m} and above.`
    : `Invalid: review ${r} is above auto-match ${m}.`;
  return (
    <div className="flex flex-col gap-2.5">
      <div
        role="img"
        aria-label={label}
        className="relative flex h-11 overflow-hidden rounded-sm border border-border text-[12px] font-semibold"
      >
        <span
          className="flex items-center overflow-hidden whitespace-nowrap bg-surface-neutral pl-2.5 text-fg-secondary"
          style={{ flex: lowEnd }}
        >
          {lowEnd > 0.12 && low}
        </span>
        {valid && (
          <span
            className="flex items-center justify-center overflow-hidden whitespace-nowrap bg-gold-surface text-gold-dark"
            style={{ flex: m - r }}
          >
            {m - r > 0.12 && "◆ Review"}
          </span>
        )}
        <span
          className="flex items-center justify-center overflow-hidden whitespace-nowrap bg-green-surface text-green-dark"
          style={{ flex: 1 - m }}
        >
          {1 - m > 0.12 && "✓ Auto-match"}
        </span>
        <span aria-hidden className="absolute inset-y-0 w-0.5 bg-green" style={{ left: pctOf(m) }} />
        <span
          aria-hidden
          className={cn("absolute inset-y-0 w-0.5", valid ? "bg-gold" : "bg-red")}
          style={{ left: pctOf(r) }}
        />
      </div>
      <div className="tabular relative h-4 text-[11px] font-medium text-fg-muted" aria-hidden>
        <span className="absolute left-0">0</span>
        <span className="absolute -translate-x-1/2 whitespace-nowrap text-green-dark" style={{ left: pctOf(m) }}>
          match {m.toFixed(2)}
        </span>
        <span
          className={cn(
            "absolute whitespace-nowrap",
            valid ? "-translate-x-1/2 text-gold-dark" : "translate-x-3 text-red-dark",
          )}
          style={{
            left: pctOf(r),
            top: valid && Math.abs(m - r) < 0.12 ? 14 : 0,
          }}
        >
          review {r.toFixed(2)}
        </span>
        <span className="absolute right-0">1</span>
      </div>
    </div>
  );
}
