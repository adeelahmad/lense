import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { auth } from "@/auth";
import { LoginForm } from "@/components/auth/login-form";
import { isSetupRequired } from "@/lib/auth/status";
import { safeCallbackUrl } from "@/lib/definitions";

export const metadata: Metadata = { title: "Sign in" };

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ callbackUrl?: string; reset?: string }>;
}) {
  const { callbackUrl, reset } = await searchParams;
  const destination = safeCallbackUrl(callbackUrl);

  const session = await auth();
  if (session && !session.error) redirect(destination);

  return (
    <LoginForm
      callbackUrl={destination}
      setupRequired={await isSetupRequired()}
      notice={
        reset
          ? "Your password was changed. Sign in with the new one."
          : undefined
      }
    />
  );
}
