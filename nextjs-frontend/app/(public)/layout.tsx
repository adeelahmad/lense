import { SessionProvider } from "next-auth/react";

import { auth } from "@/auth";
import { PublicProviders, PublicShell } from "@/components/public/public-shell";

/** Pages for visitors (docs/access.md): nobody needs to be signed in, and someone who is may see more. */
export default async function PublicLayout({ children }: { children: React.ReactNode }) {
  const session = await auth();
  const signedIn = Boolean(session && !session.error);
  return (
    <SessionProvider session={signedIn ? session : null} refetchInterval={5 * 60}>
      <PublicProviders>
        <PublicShell signedIn={signedIn} name={signedIn ? (session?.user?.name ?? session?.user?.email) : null}>
          {children}
        </PublicShell>
      </PublicProviders>
    </SessionProvider>
  );
}
