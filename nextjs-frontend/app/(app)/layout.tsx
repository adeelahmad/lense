import { redirect } from "next/navigation";
import { SessionProvider } from "next-auth/react";

import { auth } from "@/auth";
import { AppShell } from "@/components/app-shell/app-shell";
import { SessionGuard } from "@/components/app-shell/session-guard";
import { AppProviders } from "@/components/providers";

/** Every page in this group requires a session (proxy.ts also enforces it). */
export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const session = await auth();
  if (!session || session.error) redirect("/login");

  return (
    // Polling keeps the access token fresh for client-side calls (e.g. SSE streams).
    <SessionProvider session={session} refetchInterval={5 * 60}>
      <SessionGuard />
      <AppProviders>
        <AppShell user={session.user}>{children}</AppShell>
      </AppProviders>
    </SessionProvider>
  );
}
