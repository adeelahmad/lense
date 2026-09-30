import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { PasswordResetConfirmForm } from "@/components/auth/password-reset-confirm-form";

export const metadata: Metadata = { title: "Choose a new password" };

export default async function PasswordResetConfirmPage({
  searchParams,
}: {
  searchParams: Promise<{ token?: string }>;
}) {
  const { token } = await searchParams;
  if (!token) notFound();
  return <PasswordResetConfirmForm token={token} />;
}
