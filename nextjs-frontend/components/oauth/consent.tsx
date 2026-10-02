"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { Oauth } from "@/app/openapi-client";
import { AuthAlert, AuthCard } from "@/components/auth/auth-card";
import { canWrite, returnsTo, safeToFollow, type ConsentRequest } from "@/components/oauth/model";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/field";
import { Skeleton } from "@/components/ui/states";
import { data, useApiClient } from "@/lib/api/browser";

/** Sends the browser to the app's address. Its own function so tests can watch it. */
export const leave = {
  to(url: string) {
    window.location.assign(url);
  },
};

/** Which app asks, for what, and Allow or Deny (docs/authentication.md#oauth). Nobody is sent anywhere until the
 * server has checked the app and its address. */
export function ConsentCard({ request, email }: { request: ConsentRequest | null; email: string }) {
  const client = useApiClient();
  const [write, setWrite] = useState(true);
  const [stuck, setStuck] = useState<string | null>(null);
  const info = useQuery({
    queryKey: ["oauth", "consent", request],
    enabled: Boolean(request),
    retry: false,
    queryFn: () =>
      data(
        Oauth.consent({
          client,
          query: {
            client_id: request!.client_id,
            redirect_uri: request!.redirect_uri,
            response_type: request!.response_type,
            code_challenge: request!.code_challenge,
            code_challenge_method: request!.code_challenge_method,
            scope: request!.scope,
          },
        }),
      ),
  });
  const asksWrite = canWrite(info.data?.scope);
  const answer = useMutation({
    mutationFn: (approve: boolean) =>
      data(
        Oauth.answer({
          client,
          body: { ...request!, approve, grant: approve ? (asksWrite && write ? "read write" : "read") : undefined },
        }),
      ),
    onSuccess: (r) => {
      if (safeToFollow(r.redirect_to)) leave.to(r.redirect_to);
      else setStuck("This app’s address can’t be opened from a browser.");
    },
  });

  if (!request)
    return (
      <AuthCard title="Nothing to give access to" wide>
        <AuthAlert tone="error">
          This page is opened by an app that asks for access to Lens. The link that brought you here is missing the app
          or the address to return to.
        </AuthAlert>
        <BackHome />
      </AuthCard>
    );
  if (info.isPending)
    return (
      <AuthCard title="Give an app access" wide>
        <Skeleton className="h-4 w-56" />
        <Skeleton className="h-16 w-full" />
      </AuthCard>
    );
  if (info.isError)
    return (
      <AuthCard title="This app can’t be given access" wide>
        <AuthAlert tone="error">{info.error.message}</AuthAlert>
        <p className="text-[13px] leading-[1.45] text-fg-secondary">
          Nothing was shared. If you expected this to work, go back to the app and try connecting again.
        </p>
        <BackHome />
      </AuthCard>
    );

  const app = info.data.client;
  const busy = answer.isPending || answer.isSuccess;
  return (
    <AuthCard
      wide
      title={<>Give {app.name} access?</>}
      description={
        <>
          It will use Lens as you (<span className="font-semibold text-fg">{email}</span>), with your roles: it sees
          what you see, nothing more.
        </>
      }
      footer={
        <>
          You can take this back any time under API tokens → Apps with access. Afterwards you’re returned to{" "}
          <span className="break-all font-semibold text-fg-secondary">{returnsTo(info.data.redirect_uri)}</span>.
        </>
      }
    >
      {info.data.granted && (
        <AuthAlert tone="info">You gave this app access before; allowing again replaces that.</AuthAlert>
      )}
      <ul className="flex flex-col gap-2.5 rounded-[10px] border border-border px-3.5 py-3 text-[13.5px] leading-[1.4]">
        <li className="flex flex-col gap-0.5">
          <span className="font-bold text-fg">Read</span>
          <span className="text-fg-secondary">Browse, search and chat with the resources you can read.</span>
        </li>
        {asksWrite && (
          <li className="flex items-start gap-3 border-t border-border pt-2.5">
            <span className="flex min-w-0 flex-1 flex-col gap-0.5">
              <span className="font-bold text-fg">Make changes</span>
              <span className="text-fg-secondary">Import, edit and reprocess, where your roles allow.</span>
            </span>
            <Switch checked={write} onCheckedChange={setWrite} disabled={busy} aria-label="Let it make changes" />
          </li>
        )}
      </ul>
      {app.uri && <p className="break-all text-[12.5px] text-fg-muted">The app says it’s from {app.uri}</p>}
      {(answer.isError || stuck) && <AuthAlert tone="error">{stuck ?? answer.error?.message}</AuthAlert>}
      <div className="flex flex-wrap justify-end gap-2.5">
        <Button variant="ghost" disabled={busy} onClick={() => answer.mutate(false)}>
          Deny
        </Button>
        <Button variant="primary" disabled={busy} onClick={() => answer.mutate(true)}>
          {busy ? "One moment…" : "Allow"}
        </Button>
      </div>
    </AuthCard>
  );
}

function BackHome() {
  return (
    <Link href="/" className="text-[13.5px] font-semibold text-fg-accent hover:underline">
      Back to Lens
    </Link>
  );
}
