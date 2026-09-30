import type { Metadata } from "next";
import Link from "next/link";
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
  // Nobody can sign in before the first admin exists (Access AC1).
  if (await isSetupRequired()) redirect("/setup");

  return (
    <>
      <LoginForm
        callbackUrl={destination}
        notice={reset ? "Your password was changed. Sign in with the new one." : undefined}
      />
      {/* recordings made public need no account (docs/access.md) */}
      <p className="mt-4 text-center text-[13px] text-fg-secondary">
        <Link href="/explore" className="font-semibold text-fg-accent hover:underline">
          Explore the public archive
        </Link>
      </p>
    </>
  );
}
