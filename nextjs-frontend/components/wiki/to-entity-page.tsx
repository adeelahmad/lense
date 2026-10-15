"use client";

import { useQuery } from "@tanstack/react-query";
import { Shapes } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { Entities } from "@/app/openapi-client";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { data, useApiClient } from "@/lib/api/browser";

/** /entities/5, and the notes address an entity's page had before (/notes/about/entity/5): its page, by name. */
export function ToEntityPage({ id }: { id: number }) {
  const client = useApiClient();
  const router = useRouter();
  const e = useQuery({
    queryKey: ["entity", id],
    queryFn: () => data(Entities.getEntity({ client, path: { eid: id } })),
    retry: false,
  });
  const page = e.data?.page;
  useEffect(() => {
    if (page) router.replace(page + window.location.hash);
  }, [page, router]);
  if (e.isError)
    return (
      <EmptyState icon={<Shapes />} title="This entity isn’t here" tone="error">
        It was merged into another, or it’s in a namespace you can’t read.
      </EmptyState>
    );
  return (
    <div className="mx-auto flex w-full max-w-[1120px] flex-col gap-4 px-6 py-8" aria-busy="true">
      <Skeleton className="h-10 w-1/2" />
      <Skeleton className="h-[240px] w-full" />
    </div>
  );
}
