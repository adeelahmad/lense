"use client";

import { CalendarDays } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Notes } from "@/app/openapi-client";
import { Button } from "@/components/ui/button";
import { EmptyState, SkeletonRows } from "@/components/ui/states";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { useArchive } from "@/lib/hooks/session";

/** Today here, as YYYY-MM-DD: the day of the daily note. */
export function localDay(d = new Date()): string {
  const two = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())}`;
}

/** /notes/today: opens today's note of the namespace in view (the first one you can write in, with all in view),
 * made the first time it's asked for, inside "Daily notes". */
export function TodayNote() {
  const client = useApiClient();
  const router = useRouter();
  const { namespace, namespaces, can, loaded } = useArchive();
  const ns =
    namespace && can("editor", namespace)
      ? namespace
      : (namespaces.map((n) => n.name).find((n) => can("editor", n)) ?? null);
  const [err, setErr] = useState<string | null>(null);
  const [tries, setTries] = useState(0);
  useEffect(() => {
    if (!ns) return;
    let gone = false;
    data(Notes.todayPage({ client, body: { ns, date: localDay() } }))
      .then((p) => !gone && router.replace(`/notes/${p.id}`))
      .catch((e) => !gone && setErr(e instanceof ApiError ? e.message : "Please try again."));
    return () => {
      gone = true;
    };
  }, [client, ns, router, tries]);
  if (loaded && !ns)
    return (
      <EmptyState icon={<CalendarDays />} title="No namespace to write in" className="mt-16">
        Today’s note goes in a namespace you can edit; ask an owner for editor access.
      </EmptyState>
    );
  if (err)
    return (
      <EmptyState
        icon={<CalendarDays />}
        title="Couldn’t open today’s note"
        className="mt-16"
        actions={
          <Button
            onClick={() => {
              setErr(null);
              setTries((n) => n + 1);
            }}
          >
            Try again
          </Button>
        }
      >
        {err}
      </EmptyState>
    );
  return (
    <div className="mx-auto w-full max-w-[960px] px-6 py-8">
      <p className="sr-only" role="status">
        Opening today’s note…
      </p>
      <SkeletonRows rows={4} />
    </div>
  );
}
