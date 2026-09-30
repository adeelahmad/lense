"use client";

import { TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { startTransition } from "react";

import { ErrorState, isUnreachable, ServerUnreachable } from "@/components/errors/error-states";
import { Button } from "@/components/ui/button";

/** Errors inside the app: "Can't reach Lens Archive" with automatic retries, or what failed and what to do next (AC6). */
export default function AppError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  const router = useRouter();
  const retry = () =>
    startTransition(() => {
      router.refresh();
      reset();
    });

  if (isUnreachable(error))
    return <ServerUnreachable error={error} className="min-h-[calc(100vh-64px)]" onRetry={retry} />;
  return (
    <ErrorState
      alert
      className="min-h-[calc(100vh-64px)]"
      icon={<TriangleAlert />}
      code={error.digest ? `error · ${error.digest}` : "error"}
      title="Something went wrong on this page"
      actions={
        <>
          <Button variant="primary" size="sm" onClick={retry}>
            Try again
          </Button>
          <Button asChild variant="ghost" size="sm">
            <Link href="/">Go to Home</Link>
          </Button>
        </>
      }
    >
      {error.message ? `${error.message.replace(/[.!?]?$/, ".")} ` : ""}Nothing was changed. Try again; if it keeps
      happening, an admin can check System health.
    </ErrorState>
  );
}
