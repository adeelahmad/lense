"use server";

import { Auth } from "@/app/openapi-client";
import { signIn } from "@/auth";
import { createApiClient, getErrorMessage } from "@/lib/api/client";
import { endBackendSession } from "@/lib/auth/tokens";
import { type FormState, setupSchema } from "@/lib/definitions";

/** Puts the backend's refusal under the field it's about (Access AC1: "errors sit under the field"). */
function refusal(status: number | undefined, message: string): FormState {
  if (status === 403 || /code is wrong|setup is closed/i.test(message)) {
    return {
      errors: {
        code: ["Setup is closed or the code is wrong. Copy the code again from the server log."],
      },
    };
  }
  if (/email/i.test(message)) return { errors: { email: [sentence(message)] } };
  if (/password/i.test(message)) return { errors: { password: [sentence(message)] } };
  return { server_validation_error: sentence(message) };
}

function sentence(s: string): string {
  const t = s.trim();
  return t ? t[0].toUpperCase() + t.slice(1) + (/[.!?]$/.test(t) ? "" : ".") : t;
}

/** Creates the first admin with the one-time code from the server log, signs in, and opens the setup wizard. */
export async function setup(_prev: FormState, formData: FormData): Promise<FormState> {
  const validated = setupSchema.safeParse({
    code: formData.get("code") ?? "",
    name: formData.get("name") || undefined,
    email: formData.get("email") ?? "",
    password: formData.get("password") ?? "",
  });
  if (!validated.success) {
    return { errors: validated.error.flatten().fieldErrors };
  }
  const { code, name, email, password } = validated.data;

  try {
    const { data, error, response } = await Auth.setup({
      client: createApiClient(),
      body: { code, name, email, password },
    });
    if (error || !data) {
      return refusal(response?.status, getErrorMessage(error));
    }
    // The session setup returned isn't carried over; the sign-in below starts its own.
    await endBackendSession(data.refresh_token);
  } catch (err) {
    console.error("Setup error:", err);
    return {
      server_error: "An unexpected error occurred. Please try again later.",
    };
  }

  await signIn("credentials", { email, password, redirectTo: "/welcome" });
  return undefined;
}
