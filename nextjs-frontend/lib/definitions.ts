import { z } from "zod";

/** Mirrors the backend rule (app/domain/auth.py: at least 10 characters). */
export const PASSWORD_MIN_LENGTH = 10;

const email = z
  .string()
  .trim()
  .email({ message: "Enter a valid email address." });

const newPassword = z
  .string()
  .min(PASSWORD_MIN_LENGTH, `Use at least ${PASSWORD_MIN_LENGTH} characters.`);

export const loginSchema = z.object({
  email,
  password: z.string().min(1, { message: "Enter your password." }),
});

export const setupSchema = z
  .object({
    code: z
      .string()
      .trim()
      .min(1, { message: "Enter the setup code from the server log." }),
    name: z.string().trim().max(80).optional(),
    email,
    password: newPassword,
    passwordConfirm: z.string(),
  })
  .refine((data) => data.password === data.passwordConfirm, {
    message: "Passwords must match.",
    path: ["passwordConfirm"],
  });

export const passwordResetSchema = z.object({ email });

export const passwordResetConfirmSchema = z
  .object({
    token: z
      .string()
      .min(1, { message: "The reset link is missing its token." }),
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
    }
  | undefined;

/** Only same-origin paths are allowed as post-login destinations. */
export function safeCallbackUrl(value: unknown): string {
  return typeof value === "string" && /^\/(?![/\\])/.test(value) ? value : "/";
}
