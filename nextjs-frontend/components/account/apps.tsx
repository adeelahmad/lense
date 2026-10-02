"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AppWindow } from "lucide-react";
import { useState } from "react";

import { Oauth } from "@/app/openapi-client";
import type { OAuthGrant } from "@/app/openapi-client/types.gen";
import { tokenExpiry } from "@/components/account/token-model";
import { ACCESS_LABEL, appHost } from "@/components/oauth/model";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { EmptyState, SkeletonRows } from "@/components/ui/states";
import { Table, Td, Th, THead, Tr } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { relative, shortDate } from "@/lib/format";

/** The apps this person gave access to through OAuth (docs/authentication.md#oauth), each with Revoke. */
export function ConnectedApps() {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const [revoking, setRevoking] = useState<OAuthGrant | null>(null);
  const grants = useQuery({
    queryKey: ["oauth", "grants"],
    queryFn: () => data(Oauth.listGrants({ client })),
    staleTime: 0, // apps are given access in another tab
  });
  const revoke = useMutation({
    mutationFn: (g: OAuthGrant) => data(Oauth.revokeGrant({ client, path: { grant_id: g.id } })),
    onSuccess: (_r, g) => {
      setRevoking(null);
      void qc.invalidateQueries({ queryKey: ["oauth", "grants"] });
      toast({ title: `${g.name} no longer has access`, body: "Its tokens stopped working.", tone: "green" });
    },
  });
  const list = grants.data ?? [];

  return (
    <div className="px-4 pb-5 sm:px-6">
      <section
        aria-labelledby="apps-heading"
        className="mx-auto flex max-w-[1000px] flex-col gap-3.5 rounded-md border border-border bg-background px-4 py-5 sm:px-6"
      >
        <div className="flex flex-col gap-1">
          <h2 id="apps-heading" className="text-[18px] font-bold leading-[1.2] text-fg">
            Apps with access
          </h2>
          <p className="text-[13.5px] leading-[1.4] text-fg-secondary">
            Apps you signed in to with your Lens account (an assistant, an editor). They act as you, with your roles,
            until you revoke them.
          </p>
        </div>
        {grants.isPending ? (
          <SkeletonRows rows={2} />
        ) : grants.isError ? (
          <EmptyState
            tone="error"
            icon={<AppWindow />}
            title="Couldn’t load your apps"
            actions={<Button onClick={() => grants.refetch()}>Try again</Button>}
          >
            {grants.error.message}
          </EmptyState>
        ) : !list.length ? (
          <p className="text-[13px] text-fg-muted">You haven’t given any app access.</p>
        ) : (
          <div className="overflow-x-auto rounded-md border border-border">
            <Table aria-label="Apps with access" className="text-[13px]">
              <THead className="border-t-0">
                <tr>
                  <Th>App</Th>
                  <Th>Access</Th>
                  <Th>Given</Th>
                  <Th>Last used</Th>
                  <Th>Ends</Th>
                  <Th>
                    <span className="sr-only">Actions</span>
                  </Th>
                </tr>
              </THead>
              <tbody>
                {list.map((g) => (
                  <Tr key={g.id} className="h-12 last:border-b-0">
                    <Td>
                      <span className="font-semibold">{g.name}</span>
                      {appHost(g.uri) && <span className="ml-2 text-[12px] text-fg-muted">{appHost(g.uri)}</span>}
                    </Td>
                    <Td>
                      <span className="inline-flex h-[22px] items-center whitespace-nowrap rounded-pill border border-border px-2 text-[11.5px] font-semibold">
                        {ACCESS_LABEL(g.scope)}
                      </span>
                    </Td>
                    <Td className="tabular whitespace-nowrap">{shortDate(g.created_at)}</Td>
                    <Td className="tabular whitespace-nowrap text-fg-secondary">
                      {g.last_used_at ? relative(g.last_used_at) : "never"}
                    </Td>
                    <Td className="tabular whitespace-nowrap text-fg-secondary">
                      {g.expires_at ? tokenExpiry(g.expires_at).label : "—"}
                    </Td>
                    <Td className="text-right">
                      <Button
                        variant="danger-ghost"
                        size="xs"
                        onClick={() => setRevoking(g)}
                        aria-label={`Revoke ${g.name}`}
                      >
                        Revoke
                      </Button>
                    </Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
          </div>
        )}
      </section>
      <Dialog
        open={Boolean(revoking)}
        onOpenChange={(o) => {
          if (!o) {
            revoke.reset();
            setRevoking(null);
          }
        }}
        title={revoking ? `Revoke ${revoking.name}’s access?` : ""}
        actions={
          <>
            <Button variant="ghost" onClick={() => setRevoking(null)}>
              Cancel
            </Button>
            <Button variant="danger" disabled={revoke.isPending} onClick={() => revoking && revoke.mutate(revoking)}>
              {revoke.isPending ? "Working…" : "Revoke access"}
            </Button>
          </>
        }
      >
        <p className="text-[14px] leading-normal text-fg-secondary">
          The app stops working with Lens immediately. To use it again you sign in from the app and allow it once more.
        </p>
        {revoke.isError && <Banner tone="error">{revoke.error.message}</Banner>}
      </Dialog>
    </div>
  );
}
