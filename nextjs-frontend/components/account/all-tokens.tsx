"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Admin } from "@/app/openapi-client";
import type { AccountToken } from "@/app/openapi-client/types.gen";
import { SCOPE_LABEL, tokenExpiry } from "@/components/account/token-model";
import { Button } from "@/components/ui/button";
import { SkeletonRows } from "@/components/ui/states";
import { Table, Td, Th, THead, Tr } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { relative } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Everyone's API keys (admins), with Revoke: whatever uses a key stops working at once. */
export function AllTokens() {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const [confirm, setConfirm] = useState<number | null>(null);
  const list = useQuery({
    queryKey: ["tokens", "all"],
    queryFn: () => data(Admin.listAllTokens({ client })),
    staleTime: 0, // people make and use keys while the page is open
  });
  const revoke = useMutation({
    mutationFn: (t: AccountToken) => data(Admin.revokeAnyToken({ client, path: { token_id: t.id } })),
    onSuccess: (_, t) => {
      setConfirm(null);
      void qc.invalidateQueries({ queryKey: ["tokens"] });
      toast({
        title: `Revoked “${t.name}” (${t.email ?? "someone"})`,
        body: `Anything using ${t.prefix}… stops working now.`,
        tone: "green",
      });
    },
    onError: (e: Error) => toast({ title: "Couldn’t revoke the key", body: e.message, tone: "red" }),
  });
  const rows = list.data ?? [];
  return (
    <section aria-label="Everyone’s keys" className="flex flex-col gap-2">
      <h3 className="text-[14px] font-bold leading-tight text-fg">Everyone’s keys</h3>
      {list.isPending ? (
        <SkeletonRows rows={2} />
      ) : list.isError ? (
        <p className="text-[13px] text-red-dark">Couldn’t load the keys: {list.error.message}</p>
      ) : !rows.length ? (
        <p className="text-[13px] text-fg-muted">Nobody has made an API key yet.</p>
      ) : (
        <div className="overflow-x-auto rounded-md border border-border">
          <Table aria-label="Everyone’s API keys" className="text-[13px]">
            <THead className="border-t-0">
              <tr>
                <Th>Whose</Th>
                <Th>Name</Th>
                <Th>Scope</Th>
                <Th>Expires</Th>
                <Th>Last used</Th>
                <Th>
                  <span className="sr-only">Actions</span>
                </Th>
              </tr>
            </THead>
            <tbody>
              {rows.map((t) => {
                const exp = tokenExpiry(t.expires_at);
                return (
                  <Tr key={t.id} className={cn("h-11 last:border-b-0", exp.state === "expired" && "text-fg-muted")}>
                    <Td className="max-w-[200px] truncate">{t.email ?? "—"}</Td>
                    <Td className="font-semibold">
                      {t.name}{" "}
                      <code className="ml-1 font-mono text-[11.5px] font-medium text-fg-muted">{t.prefix}…</code>
                    </Td>
                    <Td className="whitespace-nowrap">{SCOPE_LABEL[t.scope] ?? t.scope}</Td>
                    <Td
                      className={cn(
                        "tabular whitespace-nowrap",
                        exp.state === "soon" ? "text-gold-dark" : exp.state !== "expired" && "text-fg-secondary",
                      )}
                    >
                      {exp.label}
                    </Td>
                    <Td className="tabular whitespace-nowrap text-fg-secondary">
                      {t.last_used_at ? relative(t.last_used_at) : "never"}
                    </Td>
                    <Td className="whitespace-nowrap text-right">
                      {confirm === t.id ? (
                        <span className="inline-flex items-center gap-1.5">
                          <Button size="xs" variant="ghost" onClick={() => setConfirm(null)}>
                            Keep
                          </Button>
                          <Button
                            size="xs"
                            variant="danger"
                            disabled={revoke.isPending}
                            onClick={() => revoke.mutate(t)}
                          >
                            Revoke
                          </Button>
                        </span>
                      ) : (
                        <Button
                          size="xs"
                          variant="danger-ghost"
                          onClick={() => setConfirm(t.id)}
                          aria-label={`Revoke ${t.name} (${t.email ?? "someone"})`}
                        >
                          Revoke…
                        </Button>
                      )}
                    </Td>
                  </Tr>
                );
              })}
            </tbody>
          </Table>
        </div>
      )}
    </section>
  );
}
