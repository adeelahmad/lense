import { redirect } from "next/navigation";
import { SessionProvider } from "next-auth/react";

import { auth } from "@/auth";
import { SessionGuard } from "@/components/app-shell/session-guard";
import { AppProviders } from "@/components/providers";

/** The first-run wizard: signed in, with the app's providers, but no app shell around it. */
export default async function SetupLayout({ children }: { children: React.ReactNode }) {
  const session = await auth();
  if (!session || session.error) redirect("/login");

  return (
    <SessionProvider session={session} refetchInterval={5 * 60}>
      <SessionGuard />
      <AppProviders>
        <main className="flex min-h-screen flex-col items-center bg-surface px-4 py-10 sm:py-14">
          <div className="w-full max-w-[640px]">{children}</div>
        </main>
      </AppProviders>
    </SessionProvider>
  );
}
