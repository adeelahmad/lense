"use client";

import { Lock } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import type { PublicRecording } from "@/app/openapi-client/types.gen";
import type { AccessPart } from "@/components/access/model";
import { closedNote } from "@/components/public/model";
import { useSignInHref } from "@/components/public/public-shell";
import { cn } from "@/lib/utils";

/** A card of a resource's public page, titled. */
export function Card({
  title,
  extra,
  children,
  className,
}: {
  title: string;
  extra?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={cn("flex flex-col gap-3 rounded-md border border-border bg-background p-4", className)}
      aria-label={title}
    >
      <div className="flex items-center gap-2">
        <h2 className="text-[14px] font-bold text-fg">{title}</h2>
        <span className="flex-1" />
        {extra}
      </div>
      {children}
    </section>
  );
}

/** What stands in for a part of a resource this visitor may not use, with a way to sign in. */
export function ClosedNote({ part, rec, signedIn }: { part: AccessPart; rec: PublicRecording; signedIn: boolean }) {
  const signIn = useSignInHref();
  const note = closedNote(part, { ns: rec.namespace, signedIn, kind: rec.media_kind });
  return (
    <div className="flex items-start gap-3 rounded-md bg-surface px-3.5 py-3">
      <Lock aria-hidden className="mt-0.5 size-4 shrink-0 text-fg-muted" />
      <div className="flex min-w-0 flex-col gap-1">
        <b className="text-[13.5px] font-semibold text-fg">{note.title}</b>
        <span className="text-[13px] leading-[1.45] text-fg-secondary">{note.body}</span>
        {!signedIn && (
          <Link href={signIn} className="w-fit text-[13px] font-semibold text-fg-accent hover:underline">
            Sign in
          </Link>
        )}
      </div>
    </div>
  );
}
