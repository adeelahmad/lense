import type { InputHTMLAttributes, ReactNode } from "react";

import { FieldError } from "@/components/ui/FormError";
import { Input } from "@/components/ui/field";
import type { FormState } from "@/lib/definitions";

type FormFieldProps = InputHTMLAttributes<HTMLInputElement> & {
  name: string;
  label: string;
  state?: FormState;
  /** Extra content under the input (e.g. a "Forgot your password?" link). */
  hint?: ReactNode;
};

/** A labelled input wired to its server-side validation errors. */
export function FormField({ name, label, state, hint, id = name, ...props }: FormFieldProps) {
  const errorId = `${id}-error`;
  const invalid = Boolean(state?.errors?.[name]?.length);
  return (
    <div className="grid gap-1.5">
      <label htmlFor={id} className="text-[13px] font-bold text-fg-strong">
        {label}
      </label>
      <Input
        id={id}
        name={name}
        aria-invalid={invalid || undefined}
        aria-describedby={invalid ? errorId : undefined}
        {...props}
      />
      <FieldError state={state} field={name} id={errorId} />
      {hint}
    </div>
  );
}
