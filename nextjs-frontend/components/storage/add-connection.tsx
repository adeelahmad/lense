"use client";

import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useState } from "react";

import { Sources } from "@/app/openapi-client";
import { ConnectionDialog } from "@/components/sources/connection-dialog";
import type { BackendSpec } from "@/components/sources/source-model";
import { Button } from "@/components/ui/button";
import { data, useApiClient } from "@/lib/api/browser";

/** Add a storage connection without leaving the page (the same dialog as Sources), and use it at once. */
export function AddConnection({
  onAdded,
  label = "Add a connection",
}: {
  onAdded: (id: number) => void;
  label?: string;
}) {
  const client = useApiClient();
  const [open, setOpen] = useState(false);
  const backends = useQuery({
    queryKey: ["source-backends"],
    queryFn: () => data(Sources.listBackends({ client })),
    staleTime: 300_000,
  });
  return (
    <>
      <Button size="sm" variant="secondary" icon={<Plus />} onClick={() => setOpen(true)} disabled={!backends.data}>
        {label}
      </Button>
      {open && backends.data && (
        <ConnectionDialog
          open
          onOpenChange={setOpen}
          backends={backends.data as Record<string, BackendSpec>}
          onSaved={(id) => {
            setOpen(false);
            onAdded(id);
          }}
        />
      )}
    </>
  );
}
