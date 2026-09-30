"use server";

import { redirect } from "next/navigation";

import { Auth } from "@/app/openapi-client";
import { createApiClient, getErrorMessage } from "@/lib/api/client";
import {
  type FormState,
  passwordResetConfirmSchema,
  passwordResetSchema,
} from "@/lib/definitions";

const UNEXPECTED = "An unexpected error occurred. Please try again later.";

export async function passwordReset(
  _prev: FormState,
  formData: FormData,
): Promise<FormState> {
  const validated = passwordResetSchema.safeParse({
    email: formData.get("email") ?? "",
  });
  if (!validated.success) {
    return { errors: validated.error.flatten().fieldErrors };
  }

  try {
    const { error } = await Auth.forgotPassword({
      client: createApiClient(),
      body: validated.data,
    });
    if (error) {
      return { server_validation_error: getErrorMessage(error) };
    }
  } catch (err) {
    console.error("Password reset error:", err);
    return { server_error: UNEXPECTED };
  }
  return {
    message: "If that address has an account, a reset link is on its way.",
  };
}

export async function passwordResetConfirm(
  _prev: FormState,
  formData: FormData,
): Promise<FormState> {
  const validated = passwordResetConfirmSchema.safeParse({
    token: formData.get("token") ?? "",
    password: formData.get("password") ?? "",
    passwordConfirm: formData.get("passwordConfirm") ?? "",
  });
  if (!validated.success) {
    return { errors: validated.error.flatten().fieldErrors };
  }
  const { token, password } = validated.data;

  try {
    const { error } = await Auth.resetPassword({
      client: createApiClient(),
      body: { token, password },
    });
    if (error) {
      return { server_validation_error: getErrorMessage(error) };
    }
  } catch (err) {
    console.error("Password reset confirmation error:", err);
    return { server_error: UNEXPECTED };
  }
  redirect("/login?reset=1");
}
