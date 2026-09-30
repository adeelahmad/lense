"use client";

import * as CheckboxPrimitive from "@radix-ui/react-checkbox";
import * as SwitchPrimitive from "@radix-ui/react-switch";
import { Check, ChevronDown, Search } from "lucide-react";
import { forwardRef, useId, useState, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";

import { Button } from "@/components/ui/button";
import { absolute } from "@/lib/format";
import { cn } from "@/lib/utils";

const control =
  "w-full rounded-sm border border-border bg-background text-[14px] text-fg placeholder:text-fg-muted outline-none transition-[border-color,box-shadow] duration-fast focus:border-blue focus:shadow-[0_0_0_3px_var(--intent-surface)] disabled:cursor-not-allowed disabled:bg-surface disabled:text-fg-muted aria-[invalid=true]:border-red aria-[invalid=true]:focus:shadow-[0_0_0_3px_var(--red-surface)]";

/** Label + control + hint or error, wired together for screen readers. */
export function Field({
  label,
  hint,
  error,
  children,
  className,
  htmlFor,
  optional,
}: {
  label?: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  children: (ids: { id: string; describedBy?: string; invalid: boolean }) => ReactNode;
  className?: string;
  htmlFor?: string;
  optional?: boolean;
}) {
  const auto = useId();
  const id = htmlFor ?? auto;
  const msgId = `${id}-msg`;
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      {label && (
        <label htmlFor={id} className="text-[13px] font-bold leading-tight text-fg-strong">
          {label}
          {optional && <span className="ml-1 font-normal text-fg-muted">optional</span>}
        </label>
      )}
      {children({ id, describedBy: error || hint ? msgId : undefined, invalid: Boolean(error) })}
      {(error || hint) && (
        <p id={msgId} className={cn("text-[12.5px] leading-snug", error ? "text-red-dark" : "text-fg-muted")} role={error ? "alert" : undefined}>
          {error || hint}
        </p>
      )}
    </div>
  );
}

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement> & { mono?: boolean; invalid?: boolean }>(function Input(
  { className, mono, invalid, ...props },
  ref,
) {
  return <input ref={ref} aria-invalid={invalid || undefined} className={cn(control, "h-10 px-3.5", mono && "font-mono text-[13px]", className)} {...props} />;
});

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement> & { mono?: boolean; invalid?: boolean }>(function Textarea(
  { className, mono, invalid, rows = 4, ...props },
  ref,
) {
  return (
    <textarea
      ref={ref}
      rows={rows}
      aria-invalid={invalid || undefined}
      className={cn(control, "min-h-[88px] resize-y px-3.5 py-2.5 leading-normal", mono && "font-mono text-[13px]", className)}
      {...props}
    />
  );
});

export type Option = { value: string; label: string; disabled?: boolean };

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement> & { options: (Option | string)[]; invalid?: boolean; size?: "sm" | "md" }>(
  function Select({ className, options, invalid, size = "md", ...props }, ref) {
    return (
      <span className={cn("relative inline-flex", className?.includes("w-") ? "" : "w-full")}>
        <select
          ref={ref}
          aria-invalid={invalid || undefined}
          className={cn(control, "appearance-none pl-3.5 pr-9", size === "sm" ? "h-8 text-[13px]" : "h-10", className)}
          {...props}
        >
          {options.map((o) => {
            const v = typeof o === "string" ? { value: o, label: o } : o;
            return (
              <option key={v.value} value={v.value} disabled={v.disabled}>
                {v.label}
              </option>
            );
          })}
        </select>
        <ChevronDown aria-hidden className="pointer-events-none absolute right-3 top-1/2 size-4 -translate-y-1/2 text-fg-secondary" />
      </span>
    );
  },
);

/** The page-level search / filter box with a leading magnifier. */
export const SearchInput = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function SearchInput({ className, ...props }, ref) {
  return (
    <span className={cn("relative block", className)}>
      <Search aria-hidden className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-fg-muted" />
      <input ref={ref} type="search" className={cn(control, "h-9 pl-9 pr-3 text-[13.5px]")} {...props} />
    </span>
  );
});

export function Checkbox({
  checked,
  onCheckedChange,
  label,
  disabled,
  className,
  "aria-label": ariaLabel,
}: {
  checked: boolean | "indeterminate";
  onCheckedChange?: (v: boolean) => void;
  label?: ReactNode;
  disabled?: boolean;
  className?: string;
  "aria-label"?: string;
}) {
  const id = useId();
  return (
    <span className={cn("inline-flex items-center gap-2.5", className)}>
      <CheckboxPrimitive.Root
        id={id}
        checked={checked}
        disabled={disabled}
        aria-label={ariaLabel}
        onCheckedChange={(v) => onCheckedChange?.(v === true)}
        className="grid size-[18px] shrink-0 place-items-center rounded-xs border-2 border-fg-secondary bg-background text-white transition-colors duration-fast data-[state=checked]:border-blue data-[state=checked]:bg-blue data-[state=indeterminate]:border-blue data-[state=indeterminate]:bg-blue disabled:cursor-not-allowed disabled:opacity-50"
      >
        <CheckboxPrimitive.Indicator>{checked === "indeterminate" ? <span className="block h-0.5 w-2 bg-white" /> : <Check className="size-3.5" strokeWidth={3} />}</CheckboxPrimitive.Indicator>
      </CheckboxPrimitive.Root>
      {label && (
        <label htmlFor={id} className="cursor-pointer text-[14px] text-fg">
          {label}
        </label>
      )}
    </span>
  );
}

export function Switch({
  checked,
  onCheckedChange,
  label,
  disabled,
  className,
  "aria-label": ariaLabel,
}: {
  checked: boolean;
  onCheckedChange?: (v: boolean) => void;
  label?: ReactNode;
  disabled?: boolean;
  className?: string;
  "aria-label"?: string;
}) {
  const id = useId();
  return (
    <span className={cn("inline-flex items-center gap-2.5", disabled && "opacity-50", className)}>
      <SwitchPrimitive.Root
        id={id}
        checked={checked}
        disabled={disabled}
        aria-label={ariaLabel}
        onCheckedChange={onCheckedChange}
        className="relative h-5 w-9 shrink-0 rounded-[10px] bg-border transition-colors duration-base ease-standard data-[state=checked]:bg-blue disabled:cursor-not-allowed"
      >
        <SwitchPrimitive.Thumb className="block size-4 translate-x-0.5 rounded-full bg-white shadow-1 transition-transform duration-base ease-standard data-[state=checked]:translate-x-[18px]" />
      </SwitchPrimitive.Root>
      {label && (
        <label htmlFor={id} className="cursor-pointer text-[14px] text-fg">
          {label}
        </label>
      )}
    </span>
  );
}

/**
 * A write-only secret (API key, token): not set · set ("updated by … on …", Replace, Clear) · replacing (Cancel).
 * `onChange(undefined)` means unchanged, `""` clears it, a string replaces it.
 */
export function SecretField({
  isSet,
  updatedBy,
  updatedAt,
  hint,
  value,
  onChange,
  disabled,
  id,
}: {
  isSet: boolean;
  updatedBy?: string | null;
  updatedAt?: string | null;
  hint?: string;
  value: string | undefined;
  onChange: (v: string | undefined) => void;
  disabled?: boolean;
  id?: string;
}) {
  const [replacing, setReplacing] = useState(false);
  const clearing = value === "";
  if (isSet && !replacing && !clearing) {
    return (
      <div className="flex flex-col gap-1.5">
        <div className="flex items-center gap-2">
          <Input id={id} value={`••••••••••••${hint ?? ""}`} readOnly disabled mono className="flex-1" aria-label="Secret is set" />
          <Button size="sm" variant="secondary" onClick={() => setReplacing(true)} disabled={disabled}>
            Replace
          </Button>
          <Button size="sm" variant="danger-ghost" onClick={() => onChange("")} disabled={disabled}>
            Clear
          </Button>
        </div>
        <p className="text-[12.5px] text-fg-muted">
          Set{updatedBy ? ` · updated by ${updatedBy}` : ""}
          {updatedAt ? ` on ${absolute(updatedAt)}` : ""} · never shown again after saving
        </p>
      </div>
    );
  }
  if (clearing) {
    return (
      <div className="flex items-center gap-2 text-[13px] text-red-dark">
        Will be cleared when you save.
        <Button size="xs" variant="ghost" onClick={() => onChange(undefined)}>
          Undo
        </Button>
      </div>
    );
  }
  return (
    <div className="flex items-center gap-2">
      <Input id={id} type="password" autoComplete="off" value={value ?? ""} onChange={(e) => onChange(e.target.value || undefined)} disabled={disabled} mono placeholder={isSet ? "New value" : "Not set"} />
      {replacing && (
        <Button
          size="sm"
          variant="ghost"
          onClick={() => {
            setReplacing(false);
            onChange(undefined);
          }}
        >
          Cancel
        </Button>
      )}
    </div>
  );
}

/** A read-only value set by the environment at startup (can't be changed in the app). */
export function EnvValue({ value, variable }: { value: ReactNode; variable?: string }) {
  return (
    <div className="flex items-center gap-2 rounded-sm border border-dashed border-border bg-surface px-3.5 py-2 text-[13px]">
      <span className="font-mono text-fg-strong">{value}</span>
      {variable && <span className="ml-auto font-mono text-[12px] text-fg-muted">{variable}</span>}
      <span className="rounded-pill bg-surface-neutral px-2 py-0.5 text-[11px] font-bold uppercase tracking-[.04em] text-fg-secondary">Set by environment</span>
    </div>
  );
}
