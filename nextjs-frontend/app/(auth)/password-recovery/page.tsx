import type { Metadata } from "next";

import { LostPasskeyForm } from "@/components/auth/lost-passkey-form";
import { PasswordResetForm } from "@/components/auth/password-reset-form";
import { passwordsOn } from "@/lib/auth/status";

export const metadata: Metadata = { title: "Get back in" };
export const dynamic = "force-dynamic";

/** A reset link where passwords sign in; a sign-in link for adding a passkey where they don't. */
export default async function PasswordRecoveryPage() {
  return (await passwordsOn()) ? <PasswordResetForm /> : <LostPasskeyForm />;
}
