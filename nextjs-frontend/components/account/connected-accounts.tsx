"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import { Auth, type ExternalIdentity } from "@/app/openapi-client";
import { ProviderIcon } from "@/components/auth/external-sign-in";
import { Banner } from "@/components/ui/banner";
import { Button, IconButton } from "@/components/ui/button";
import { Panel } from "@/components/ui/panel";
import { DateTime, Skeleton } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";

/**
 * Outside accounts you sign in with (Google, GitHub, Microsoft, OpenID Connect): connect one from a provider an admin
 * set up, or disconnect one (not your only way in). Hidden when no provider is set up and nothing is connected.
 */
export function ConnectedAccountsPanel() {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const [error, setError] = useState<string | null>(null);
  const providers = useQuery({
    queryKey: ["external-providers"],
    queryFn: () => data(Auth.externalProviders({ client })),
  });
  const list = useQuery({ queryKey: ["identities"], queryFn: () => data(Auth.listIdentities({ client })) });

  // back from connecting one (/account?connected=Google)
  useEffect(() => {
    const url = new URL(window.location.href);
    const label = url.searchParams.get("connected");
    if (!label) return;
    toast({ title: `${label} connected`, body: `You can sign in with ${label} now.`, tone: "green" });
    url.searchParams.delete("connected");
    window.history.replaceState(null, "", url.pathname + url.search);
  }, [toast]);

  const connect = useMutation({
    mutationFn: (key: string) => data(Auth.externalConnect({ client, path: { key } })),
    onSuccess: (r) => window.location.assign(r.url),
    onError: (e) => setError(e.message),
  });
  const disconnect = useMutation({
    mutationFn: (iid: string) => data(Auth.disconnectIdentity({ client, path: { iid } })),
    onSuccess: () => {
      setError(null);
      void qc.invalidateQueries({ queryKey: ["identities"] });
    },
    onError: (e) => setError(e.message),
  });

  const connected = new Set((list.data ?? []).map((i) => i.provider));
  const available = (providers.data ?? []).filter((p) => !connected.has(p.key));
  if (!providers.data?.length && !list.data?.length) return null;

  return (
    <Panel title="Connected accounts" subtitle="Sign in with an account you already have elsewhere.">
      <div className="flex flex-col gap-3">
        {error && <Banner tone="error">{error}</Banner>}
        {list.isLoading ? (
          <Skeleton className="h-12 w-full" />
        ) : list.data?.length ? (
          <ul className="flex flex-col divide-y divide-border rounded-sm border border-border">
            {list.data.map((i) => (
              <IdentityRow key={i.id} identity={i} onDisconnect={() => disconnect.mutate(i.id)} />
            ))}
          </ul>
        ) : null}
        {available.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {available.map((p) => (
              <Button
                key={p.key}
                size="sm"
                icon={<ProviderIcon kind={p.kind} />}
                onClick={() => connect.mutate(p.key)}
                disabled={connect.isPending}
              >
                {connect.isPending && connect.variables === p.key ? `Opening ${p.label}…` : `Connect ${p.label}`}
              </Button>
            ))}
          </div>
        )}
      </div>
    </Panel>
  );
}

function IdentityRow({ identity: i, onDisconnect }: { identity: ExternalIdentity; onDisconnect: () => void }) {
  const [confirming, setConfirming] = useState(false);
  return (
    <li className="flex flex-wrap items-center gap-3 px-3 py-2.5 text-[13.5px]">
      <span className="text-fg-muted [&_svg]:size-4" aria-hidden>
        <ProviderIcon kind={i.kind} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="font-medium text-fg">{i.label}</div>
        <div className="text-[12px] text-fg-muted">
          {i.email ? `${i.email} · ` : ""}connected <DateTime iso={i.created_at} />
          {i.last_used_at ? (
            <>
              {" "}
              · last used <DateTime iso={i.last_used_at} />
            </>
          ) : null}
        </div>
      </div>
      {confirming ? (
        <span className="flex items-center gap-1.5">
          <Button size="sm" variant="danger" onClick={onDisconnect}>
            Disconnect
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>
            Keep
          </Button>
        </span>
      ) : (
        <IconButton label="Disconnect" onClick={() => setConfirming(true)}>
          <Trash2 />
        </IconButton>
      )}
    </li>
  );
}
