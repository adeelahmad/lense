"use client";

import { FileQuestion } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { useSignInHref } from "@/components/public/public-shell";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/states";

export function Centered({ children }: { children: ReactNode }) {
  return <div className="mx-auto flex w-full max-w-[560px] flex-1 flex-col justify-center px-4 py-12">{children}</div>;
}

/** What visitors see for something that isn't there for them: missing, or not theirs to see (the same answer). */
export function Unavailable({ what, signedIn }: { what: "recording" | "collection"; signedIn: boolean }) {
  const signIn = useSignInHref();
  return (
    <Centered>
      <EmptyState
        icon={<FileQuestion />}
        title={`This ${what} isn’t available`}
        actions={
          <>
            {!signedIn && (
              <Button asChild variant="primary">
                <Link href={signIn}>Sign in</Link>
              </Button>
            )}
            <Button asChild>
              <Link href="/explore">Explore the archive</Link>
            </Button>
          </>
        }
      >
        {signedIn
          ? "It may not be public, or the link may be wrong."
          : "It may not be public, or you may need to sign in to see it."}
      </EmptyState>
    </Centered>
  );
}

export function LoadError({ what, message, retry }: { what: string; message: string; retry: () => void }) {
  return (
    <Centered>
      <EmptyState
        tone="error"
        icon={<FileQuestion />}
        title={`Couldn’t load ${what}`}
        actions={<Button onClick={retry}>Try again</Button>}
      >
        {message}
      </EmptyState>
    </Centered>
  );
}
