"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { LogIn } from "lucide-react";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { useState, type ReactNode } from "react";

import { Brand } from "@/components/brand";
import { Button } from "@/components/ui/button";
import { ToastProvider } from "@/components/ui/toast";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ApiError } from "@/lib/api/browser";

/** Data cache, tooltips and toasts for the pages visitors see (no archive context: they may not be signed in). */
export function PublicProviders({ children }: { children: ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 60_000,
            refetchOnWindowFocus: false,
            retry: (n, e) => n < 1 && !(e instanceof ApiError && e.status >= 400 && e.status < 500),
          },
        },
      }),
  );
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider delayDuration={250}>
        <ToastProvider>{children}</ToastProvider>
      </TooltipProvider>
    </QueryClientProvider>
  );
}

/** Where signing in from this page comes back to. */
export function useSignInHref(): string {
  const path = usePathname();
  const query = useSearchParams().toString();
  return `/login?callbackUrl=${encodeURIComponent(path + (query ? `?${query}` : ""))}`;
}

/** The frame of the pages visitors see: the wordmark, and signing in or going back to the workspace. */
export function PublicShell({
  signedIn,
  name,
  children,
}: {
  signedIn: boolean;
  name?: string | null;
  children: ReactNode;
}) {
  const signIn = useSignInHref();
  return (
    <div className="flex min-h-screen flex-col bg-surface">
      <header className="sticky top-0 z-20 border-b border-border bg-background">
        <div className="mx-auto flex h-14 w-full max-w-[1120px] items-center gap-3 px-4 sm:px-6">
          <Brand href="/explore" size={17} />
          <span className="flex-1" />
          {signedIn ? (
            <>
              {name && <span className="hidden truncate text-[13px] text-fg-secondary sm:block">{name}</span>}
              <Button asChild size="sm">
                <Link href="/">Open the workspace</Link>
              </Button>
            </>
          ) : (
            <Button asChild size="sm">
              <Link href={signIn}>
                <LogIn aria-hidden />
                Sign in
              </Link>
            </Button>
          )}
        </div>
      </header>
      <main className="flex w-full flex-1 flex-col">{children}</main>
    </div>
  );
}
