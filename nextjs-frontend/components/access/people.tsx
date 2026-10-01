"use client";

import { UserRound, X } from "lucide-react";
import { useState, type FormEvent } from "react";

import {
  useAccessRequests,
  useDecideRequest,
  useGivePermission,
  usePermissions,
  useTakePermission,
} from "@/components/access/hooks";
import { permissionLine } from "@/components/access/model";
import { Button, IconButton } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { Skeleton } from "@/components/ui/states";
import { relative } from "@/lib/format";

/**
 * People given permission on one recording (docs/access.md): they see all of it on the pages visitors see and in
 * IIIF, whatever its access. Owners give it by email address and take it away here.
 */
export function PeopleWithPermission({ rid, ns }: { rid: number; ns: string }) {
  const q = usePermissions(rid);
  const give = useGivePermission(rid);
  const take = useTakePermission(rid);
  const [email, setEmail] = useState("");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!email.trim()) return;
    give.mutate(email, { onSuccess: () => setEmail("") });
  };
  return (
    <section
      aria-labelledby="people-heading"
      className="flex flex-col gap-2.5 rounded-md border border-border px-3 py-3"
    >
      <div className="flex flex-col gap-0.5">
        <h3 id="people-heading" className="text-[13px] font-bold text-fg-strong">
          People with permission
        </h3>
        <p className="text-[12.5px] leading-[1.45] text-fg-secondary">
          They see all of it on the pages visitors see and in IIIF, whatever its access. Members of{" "}
          <b className="font-semibold text-fg">{ns}</b> already do.
        </p>
      </div>
      <form onSubmit={submit} className="flex gap-2">
        <Input
          type="email"
          aria-label="Their email address"
          placeholder="Their email address"
          autoComplete="off"
          value={email}
          onChange={(e) => {
            setEmail(e.target.value);
            give.reset();
          }}
          aria-invalid={give.isError || undefined}
          className="min-w-0 flex-1"
        />
        <Button
          type="submit"
          size="sm"
          className="h-9"
          disabled={!email.trim() || give.isPending}
          disabledReason={!email.trim() ? "Type an email address first" : undefined}
        >
          {give.isPending ? "Giving…" : "Give permission"}
        </Button>
      </form>
      {give.isError && (
        <p role="alert" className="text-[12.5px] text-red-dark">
          {give.error.message}
        </p>
      )}
      {q.isPending ? (
        <Skeleton className="h-10 w-full" />
      ) : q.isError ? (
        <p className="text-[12.5px] text-red-dark">{q.error.message}</p>
      ) : q.data.length ? (
        <ul aria-label="People with permission" className="flex flex-col">
          {q.data.map((p) => (
            <li key={p.account} className="flex items-center gap-2.5 border-t border-border py-2 first:border-t-0">
              <span className="grid size-7 shrink-0 place-items-center rounded-full bg-surface-neutral text-fg-secondary">
                <UserRound aria-hidden className="size-3.5" />
              </span>
              <span className="flex min-w-0 flex-1 flex-col">
                <span className="truncate text-[13px] font-semibold text-fg">{p.name || p.email}</span>
                <span className="truncate text-[12px] text-fg-muted">
                  {p.name ? `${p.email} · ` : ""}
                  {permissionLine(p)}
                </span>
              </span>
              <IconButton
                label={`Take ${p.email}’s permission away`}
                size={30}
                disabled={take.isPending}
                onClick={() => take.mutate(p)}
              >
                <X />
              </IconButton>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-[12.5px] text-fg-muted">Nobody yet.</p>
      )}
    </section>
  );
}

/** Requests for access waiting for an owner's answer (gold ◆: a person decides). Approving gives permission. */
export function RequestsWaiting({ rid }: { rid: number }) {
  const q = useAccessRequests(rid);
  const decide = useDecideRequest(rid);
  const pending = (q.data ?? []).filter((r) => r.status === "pending");
  if (!pending.length) return null;
  return (
    <section
      aria-labelledby="requests-heading"
      className="flex flex-col gap-2 rounded-md border border-gold-border bg-gold-surface px-3 py-3"
    >
      <h3 id="requests-heading" className="flex items-center gap-1.5 text-[13px] font-bold text-fg-strong">
        <span aria-hidden className="text-gold-dark">
          ◆
        </span>
        {pending.length === 1 ? "Someone asks for access" : `${pending.length} people ask for access`}
      </h3>
      <ul aria-label="Requests for access" className="flex flex-col">
        {pending.map((r) => (
          <li
            key={r.account}
            className="flex flex-wrap items-start gap-x-3 gap-y-2 border-t border-gold-border py-2 first:border-t-0"
          >
            <span className="flex min-w-0 flex-1 basis-[220px] flex-col gap-0.5">
              <span className="truncate text-[13px] font-semibold text-fg">{r.name || r.email}</span>
              <span className="truncate text-[12px] text-fg-secondary">
                {r.name ? `${r.email} · ` : ""}asked {relative(r.at)}
              </span>
              {r.message && <q className="text-[12.5px] leading-[1.45] text-fg">{r.message}</q>}
            </span>
            <span className="flex gap-2">
              <Button
                size="sm"
                variant="primary"
                disabled={decide.isPending}
                onClick={() => decide.mutate({ req: r, approve: true })}
                aria-label={`Approve ${r.email}`}
              >
                Approve
              </Button>
              <Button
                size="sm"
                disabled={decide.isPending}
                onClick={() => decide.mutate({ req: r, approve: false })}
                aria-label={`Decline ${r.email}`}
              >
                Decline
              </Button>
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
