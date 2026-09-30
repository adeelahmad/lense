import type { Metadata } from "next";

import { PasswordResetForm } from "@/components/auth/password-reset-form";

export const metadata: Metadata = { title: "Reset password" };

export default function PasswordRecoveryPage() {
  return <PasswordResetForm />;
}
