"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { ToastProvider } from "@/components/ui/toast";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ApiError } from "@/lib/api/browser";
import { ArchiveProvider } from "@/lib/hooks/session";

/** Client-side providers for the signed-in app: data cache, archive context, tooltips, toasts. */
export function AppProviders({ children }: { children: ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 15_000,
            refetchOnWindowFocus: false,
            // 4xx answers won't change on retry; network blips and 5xx get one more try.
            retry: (n, e) => n < 1 && !(e instanceof ApiError && e.status >= 400 && e.status < 500),
          },
        },
      }),
  );
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider delayDuration={250}>
        <ToastProvider>
          <ArchiveProvider>{children}</ArchiveProvider>
        </ToastProvider>
      </TooltipProvider>
    </QueryClientProvider>
  );
}
