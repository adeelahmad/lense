"use client";

import Link from "next/link";
import { useState } from "react";

import { defaultImportNamespace, isFileDrag, useSendToImport } from "@/components/import/pending";
import { Button } from "@/components/ui/button";
import { needRole, useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

/** Home's quick import: drop files or choose them; they open in Import with a preview before anything is saved. */
export function QuickImport() {
  const { namespaces, namespace, can, me } = useArchive();
  const target = defaultImportNamespace(namespaces, (n) => can("editor", n), namespace);
  const send = useSendToImport(target);
  const [over, setOver] = useState(false);
  // Until we know who's signed in, don't claim they can't import.
  const loading = !me;
  const allowed = can("editor");
  return (
    <section
      aria-labelledby="quick-import"
      onDragOver={(e) => {
        if (allowed && isFileDrag(e)) {
          e.preventDefault();
          setOver(true);
        }
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        if (!allowed || !isFileDrag(e)) return;
        e.preventDefault();
        setOver(false);
        send.open(Array.from(e.dataTransfer.files));
      }}
      className={cn(
        "flex flex-col items-start gap-2.5 rounded-lg border-[1.5px] border-dashed border-blue-border p-[18px]",
        over && "border-blue bg-blue-surface",
      )}
    >
      <h2 id="quick-import" className="text-[15px] font-bold leading-tight text-fg">
        Quick import
      </h2>
      <p className="text-[13px] leading-[1.45] text-fg-secondary">
        {allowed || loading ? (
          <>
            Drop transcripts here, or{" "}
            <Link href="/import?tab=paste" className="font-semibold text-fg-accent hover:underline">
              paste text
            </Link>
            .
            {target ? (
              <>
                {" "}
                They go into <b className="font-semibold text-fg-strong">{target}</b> unless you pick another namespace.
              </>
            ) : (
              " You’ll see how each file was read before anything is saved."
            )}
          </>
        ) : (
          "Importing needs the editor role in a namespace. Ask an owner to make you an editor."
        )}
      </p>
      <Button
        variant="primary"
        size="sm"
        onClick={send.pick}
        disabled={!allowed}
        disabledReason={loading ? undefined : needRole("editor", namespace)}
      >
        Choose files
      </Button>
    </section>
  );
}
