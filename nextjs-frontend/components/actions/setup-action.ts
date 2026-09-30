"use server";

import { Auth } from "@/app/openapi-client";
import { signIn } from "@/auth";
import { createApiClient, getErrorMessage } from "@/lib/api/client";
import { endBackendSession } from "@/lib/auth/tokens";
import { type FormState, setupSchema } from "@/lib/definitions";

/** Creates the first admin with the one-time code from the server log, then signs in. */
export async function setup(
  _prev: FormState,
  formData: FormData,
): Promise<FormState> {
  const validated = setupSchema.safeParse({
    code: formData.get("code") ?? "",
    name: formData.get("name") || undefined,
    email: formData.get("email") ?? "",
    password: formData.get("password") ?? "",
    passwordConfirm: formData.get("passwordConfirm") ?? "",
  });
  if (!validated.success) {
    return { errors: validated.error.flatten().fieldErrors };
  }
  const { code, name, email, password } = validated.data;

  try {
    const { data, error } = await Auth.setup({
      client: createApiClient(),
      body: { code, name, email, password },
    });
    if (error || !data) {
      return { server_validation_error: getErrorMessage(error) };
    }
    // The session setup returned isn't carried over; the sign-in below starts its own.
    await endBackendSession(data.refresh_token);
  } catch (err) {
    console.error("Setup error:", err);
    return {
      server_error: "An unexpected error occurred. Please try again later.",
    };
  }

  await signIn("credentials", { email, password, redirectTo: "/" });
  return undefined;
}
