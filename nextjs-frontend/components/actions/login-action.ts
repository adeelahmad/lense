"use server";

import { AuthError, CredentialsSignin } from "next-auth";

import { signIn } from "@/auth";
import {
  type FormState,
  loginSchema,
  safeCallbackUrl,
} from "@/lib/definitions";

const UNEXPECTED = "An unexpected error occurred. Please try again later.";

export async function login(
  _prev: FormState,
  formData: FormData,
): Promise<FormState> {
  const validated = loginSchema.safeParse({
    email: formData.get("email") ?? "",
    password: formData.get("password") ?? "",
  });
  if (!validated.success) {
    return { errors: validated.error.flatten().fieldErrors };
  }

  try {
    // Redirects (by throwing) on success.
    await signIn("credentials", {
      ...validated.data,
      redirectTo: safeCallbackUrl(formData.get("callbackUrl")),
    });
  } catch (err) {
    if (err instanceof CredentialsSignin) {
      // The backend's own messages (429 and 401 from /auth/login).
      return err.code === "throttled"
        ? {
            server_validation_error:
              "Too many attempts; try again in a few minutes.",
            throttled: true,
          }
        : { server_validation_error: "Wrong email or password." };
    }
    if (err instanceof AuthError) {
      console.error("Sign-in error:", err);
      return { server_error: UNEXPECTED };
    }
    throw err;
  }
  return undefined;
}
