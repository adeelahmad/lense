import type { FormState } from "@/lib/definitions";
import { cn } from "@/lib/utils";

/** The form-level error (server rejected the request or failed). */
export function FormError({
  state,
  className,
}: {
  state?: FormState;
  className?: string;
}) {
  const error = state?.server_validation_error || state?.server_error;
  if (!error) return null;
  return (
    <p role="alert" className={cn("text-sm text-red-dark", className)}>
      {error}
    </p>
  );
}

/** Validation errors for one field; `id` is what the input's aria-describedby points at. */
export function FieldError({
  state,
  field,
  id,
  className,
}: {
  state?: FormState;
  field: string;
  id?: string;
  className?: string;
}) {
  const errors = state?.errors?.[field];
  if (!errors?.length) return null;
  return (
    <div id={id} className={cn("text-sm text-red-dark", className)}>
      {errors.length === 1 ? (
        <p>{errors[0]}</p>
      ) : (
        <ul className="ml-4 list-disc">
          {errors.map((err) => (
            <li key={err}>{err}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
