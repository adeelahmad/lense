import { z } from "zod";

/** Mirrors the backend rule (app/domain/auth.py: at least 10 characters). */
export const PASSWORD_MIN_LENGTH = 10;

const email = z.string().trim().email({ message: "Enter a valid email address." });

const newPassword = z.string().min(PASSWORD_MIN_LENGTH, `Use at least ${PASSWORD_MIN_LENGTH} characters.`);

export const loginSchema = z.object({
  email,
  password: z.string().min(1, { message: "Enter your password." }),
});

/**
 * The password rule as you type: null while it's fine (or still empty), otherwise
 * "9 of 10 characters — add at least 1 more".
 */
export function passwordShortBy(password: string): string | null {
  const n = [...password].length;
  if (!n || n >= PASSWORD_MIN_LENGTH) return null;
  const more = PASSWORD_MIN_LENGTH - n;
  return `${n} of ${PASSWORD_MIN_LENGTH} characters — add at least ${more} more`;
}

export const passwordResetSchema = z.object({ email });

export const passwordResetConfirmSchema = z
  .object({
    token: z.string().min(1, { message: "The reset link is missing its token." }),
    password: newPassword,
    passwordConfirm: z.string(),
  })
  .refine((data) => data.password === data.passwordConfirm, {
    message: "Passwords must match.",
    path: ["passwordConfirm"],
  });

/** What form server actions return to `useActionState`. */
export type FormState =
  | {
      errors?: Record<string, string[] | undefined>;
      server_validation_error?: string;
      server_error?: string;
      message?: string;
      /** Sign-in was refused for too many attempts; the form waits before trying again. */
      throttled?: boolean;
    }
  | undefined;

/** Only same-origin paths are allowed as post-login destinations. Browsers drop tabs and line breaks from a URL, so
 * "/\t/evil.example" would become "//evil.example": control characters and backslashes are refused anywhere. */
export function safeCallbackUrl(value: unknown): string {
  // eslint-disable-next-line no-control-regex
  return typeof value === "string" && /^\/(?![/\\])/.test(value) && !/[\x00-\x1f\x7f\\]/.test(value) ? value : "/";
}
