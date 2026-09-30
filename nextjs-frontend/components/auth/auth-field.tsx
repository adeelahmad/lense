"use client";

import { forwardRef, type InputHTMLAttributes, type ReactNode } from "react";

import { Input } from "@/components/ui/field";
import type { FormState } from "@/lib/definitions";
import { cn } from "@/lib/utils";

type Props = InputHTMLAttributes<HTMLInputElement> & {
  name: string;
  label: string;
  /** Server-side validation errors for this field come from here. */
  state?: FormState;
  /** A client-side error (checked as you type); shown instead of the server's. */
  error?: string | null;
  hint?: ReactNode;
  mono?: boolean;
};

/**
 * A labelled input for the signed-out pages. The message under it (hint or error) is its accessible description,
 * and is a polite live region so rules checked as you type are announced without interrupting.
 */
export const AuthField = forwardRef<HTMLInputElement, Props>(function AuthField(
  { name, label, state, error, hint, mono, id = name, className, ...props },
  ref,
) {
  const msgId = `${id}-msg`;
  const message = error || state?.errors?.[name]?.[0];
  const invalid = Boolean(message);
  const text = message || hint;
  return (
    <div className={cn("grid gap-1.5", className)}>
      <label htmlFor={id} className="text-[13px] font-bold leading-tight text-fg-strong">
        {label}
      </label>
      <Input
        ref={ref}
        id={id}
        name={name}
        mono={mono}
        aria-invalid={invalid || undefined}
        aria-describedby={text ? msgId : undefined}
        {...props}
      />
      <p
        id={msgId}
        aria-live="polite"
        className={cn("text-[12.5px] leading-snug empty:hidden", invalid ? "text-red-dark" : "text-fg-muted")}
      >
        {text}
      </p>
    </div>
  );
});
