import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { SessionProvider } from "next-auth/react";

import { auth } from "@/auth";
import { ConsentCard } from "@/components/oauth/consent";
import { consentRequest } from "@/components/oauth/model";
import { AppProviders } from "@/components/providers";

export const metadata: Metadata = { title: "Give an app access", robots: { index: false } };

/**
 * The OAuth consent page (docs/authentication.md#oauth): an app sends someone here; signed in, they see which app
 * asks, for what, and say yes or no. proxy.ts sends signed-out people to sign in first and brings them back.
 */
export default async function AuthorizePage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const session = await auth();
  if (!session || session.error) {
    const query = new URLSearchParams(
      Object.entries(params).flatMap(([k, v]) => (v === undefined ? [] : [[k, Array.isArray(v) ? v[0] : v]])),
    );
    redirect(`/login?callbackUrl=${encodeURIComponent(`/oauth/authorize?${query}`)}`);
  }
  return (
    // no refetch on focus: leaving for the app's address would start one and cut it off
    <SessionProvider session={session} refetchOnWindowFocus={false}>
      <AppProviders>
        <main className="flex min-h-screen flex-col items-center justify-center bg-surface px-4 py-12">
          <div className="w-full max-w-[440px]">
            <ConsentCard request={consentRequest(params)} email={session.user.email} />
          </div>
        </main>
      </AppProviders>
    </SessionProvider>
  );
}
