"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fingerprint, Lock, LockOpen, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import { Vaults, type VaultStatus } from "@/app/openapi-client";
import { Badge } from "@/components/ui/badge";
import { Banner } from "@/components/ui/banner";
import { Button, IconButton } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { passkeyErrorMessage, passkeysUnavailableReason, signWithPasskeyPrf } from "@/lib/auth/webauthn";
import { needRole } from "@/lib/hooks/session";

type Kind = "seal" | "unlock" | "add";

/** "until 14:05" for a Unix time. */
export function openUntil(unix: number | null | undefined): string {
  if (!unix) return "";
  return `until ${new Date(unix * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
}

/**
 * A namespace's vault (docs/encryption.md#vaults), on its page: owners lock it to their passkeys so the server can't
 * open its files alone, add and remove the passkeys that open it, or make it ordinary again. Anyone whose passkey
 * opens it unlocks it for a while, or locks it now.
 */
export function VaultSection({ ns, isOwner }: { ns: string; isOwner: boolean }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<"seal" | "unseal" | null>(null);
  const [unavailable, setUnavailable] = useState<string | null>(null);
  useEffect(() => setUnavailable(passkeysUnavailableReason()), []);
  const key = ["vault", ns];
  const q = useQuery({ queryKey: key, queryFn: () => data(Vaults.getVault({ client, path: { name: ns } })) });
  const done = (st: VaultStatus) => {
    setError(null);
    setConfirm(null);
    qc.setQueryData(key, st);
  };
  const fail = (e: unknown) => {
    setConfirm(null);
    setError(passkeyErrorMessage(e));
  };

  // every step that needs a passkey: options, the passkey's answer and its secret for this vault, then the answer
  const withPasskey = useMutation({
    mutationFn: async (kind: Kind) => {
      const path = { name: ns };
      const start = await data(Vaults.vaultOptions({ client, path, body: { kind } }));
      const body = { flow: start.flow, ...(await signWithPasskeyPrf(start.options)) };
      if (kind === "seal") return data(Vaults.sealVault({ client, path, body }));
      if (kind === "unlock") return data(Vaults.unlockVault({ client, path, body }));
      return data(Vaults.addVaultPasskey({ client, path, body }));
    },
    onSuccess: (st, kind) => {
      done(st);
      toast({
        title: { seal: `${ns} is a vault now`, unlock: `${ns} is unlocked`, add: "Passkey added to the vault" }[kind],
        body: kind === "seal" ? "Add a second passkey, so losing one device doesn't lose its files." : undefined,
        tone: "green",
      });
    },
    onError: fail,
  });
  const lock = useMutation({
    mutationFn: () => data(Vaults.lockVault({ client, path: { name: ns } })),
    onSuccess: done,
    onError: fail,
  });
  const unseal = useMutation({
    mutationFn: () => data(Vaults.unsealVault({ client, path: { name: ns } })),
    onSuccess: (st) => {
      done(st);
      toast({ title: `${ns} is an ordinary namespace again`, tone: "green" });
    },
    onError: fail,
  });
  const remove = useMutation({
    mutationFn: (pid: string) => data(Vaults.removeVaultPasskey({ client, path: { name: ns, pid } })),
    onSuccess: done,
    onError: fail,
  });

  const st = q.data;
  const keys = st?.passkeys ?? [];
  const busy = withPasskey.isPending || lock.isPending || unseal.isPending || remove.isPending;
  const waiting = (kind: Kind) => withPasskey.isPending && withPasskey.variables === kind;
  const ownerOnly = isOwner ? undefined : needRole("owner", ns);

  return (
    <section
      id="vault"
      aria-labelledby="vault-heading"
      className="flex scroll-mt-4 flex-col gap-3.5 rounded-md border border-border bg-background px-4 py-5 sm:px-6"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 flex-1 basis-[260px] flex-col gap-1">
          <h2 id="vault-heading" className="flex items-center gap-2 text-[17px] font-bold leading-tight text-fg">
            Vault
            {st?.vault &&
              (st.unlocked ? (
                <Badge tone="green" dot>
                  Unlocked {openUntil(st.unlocked_until)}
                </Badge>
              ) : (
                <Badge tone="gate" dot>
                  Locked
                </Badge>
              ))}
          </h2>
          <p className="text-[13px] leading-[1.45] text-fg-secondary">
            {st?.vault
              ? `Only the passkeys below open ${ns}’s files. While it’s locked they don’t play or download, and its queued work waits.`
              : `Lock ${ns}’s files to your passkey, so the server can’t open them without you. Background work runs only while it’s unlocked.`}
          </p>
        </div>
        {st && (
          <div className="flex flex-wrap gap-2">
            {!st.vault && (
              <Button
                size="sm"
                icon={<Lock />}
                disabled={!isOwner || busy || Boolean(unavailable)}
                disabledReason={ownerOnly ?? unavailable ?? undefined}
                onClick={() => setConfirm("seal")}
              >
                Lock to my passkey
              </Button>
            )}
            {st.vault && !st.unlocked && (
              <Button
                size="sm"
                variant="primary"
                icon={<LockOpen />}
                disabled={busy || Boolean(unavailable)}
                disabledReason={unavailable ?? undefined}
                onClick={() => withPasskey.mutate("unlock")}
              >
                {waiting("unlock") ? "Waiting for your passkey…" : "Unlock with my passkey"}
              </Button>
            )}
            {st.vault && st.unlocked && (
              <Button size="sm" icon={<Lock />} disabled={busy} onClick={() => lock.mutate()}>
                Lock now
              </Button>
            )}
          </div>
        )}
      </div>
      {error && <Banner tone="error">{error}</Banner>}
      {q.isPending ? (
        <Skeleton className="h-10 w-full" />
      ) : q.isError ? (
        <Banner tone="error">{q.error.message}</Banner>
      ) : st?.vault ? (
        <>
          <ul className="flex flex-col divide-y divide-border rounded-sm border border-border">
            {keys.map((p) => (
              <li key={p.id} className="flex items-center gap-3 px-3 py-2.5 text-[13.5px]">
                <Fingerprint className="size-4 text-fg-muted" aria-hidden />
                <div className="min-w-0 flex-1">
                  <div className="font-medium text-fg">{p.name || "A passkey"}</div>
                  {p.email && <div className="text-[12px] text-fg-muted">{p.email}</div>}
                </div>
                {isOwner && (
                  <IconButton
                    label={
                      keys.length === 1
                        ? "The only passkey that opens it: add another before removing this one"
                        : "Stop this passkey opening the vault"
                    }
                    disabled={keys.length === 1 || busy}
                    onClick={() => remove.mutate(p.id)}
                  >
                    <Trash2 />
                  </IconButton>
                )}
              </li>
            ))}
          </ul>
          {isOwner && (
            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                icon={<Fingerprint />}
                disabled={!st.unlocked || busy || Boolean(unavailable)}
                disabledReason={!st.unlocked ? "Unlock it first" : (unavailable ?? undefined)}
                onClick={() => withPasskey.mutate("add")}
              >
                {waiting("add") ? "Waiting for your passkey…" : "Add one of my passkeys"}
              </Button>
              <Button
                size="sm"
                variant="danger-ghost"
                disabled={!st.unlocked || busy}
                disabledReason={!st.unlocked ? "Unlock it first" : undefined}
                onClick={() => setConfirm("unseal")}
              >
                Make it ordinary again
              </Button>
            </div>
          )}
          {keys.length === 1 && (
            <p className="text-[12.5px] text-fg-muted">
              One passkey opens it. Add another (a phone or a security key), or losing this one loses its files.
            </p>
          )}
        </>
      ) : null}

      <Dialog
        open={confirm === "seal"}
        onOpenChange={(o) => !o && setConfirm(null)}
        title={`Lock ${ns} to your passkey?`}
        description="The server stops keeping a key that opens it. Your passkey (and others you add) become the only way in."
        actions={
          <>
            <Button variant="ghost" onClick={() => setConfirm(null)}>
              Cancel
            </Button>
            <Button variant="primary" disabled={busy} onClick={() => withPasskey.mutate("seal")}>
              {waiting("seal") ? "Waiting for your passkey…" : "Lock it"}
            </Button>
          </>
        }
      >
        <ul className="flex list-disc flex-col gap-1.5 pl-5 text-[13.5px] leading-[1.45] text-fg-secondary">
          <li>There is no recovery code or password. If every passkey that opens it is lost, its files are gone.</li>
          <li>It stays unlocked for a while after each unlock, and its queued work runs only then.</li>
          <li>
            Its files are encrypted now, if they aren’t already. Transcripts, search and other details stay in the
            database, which the vault doesn’t cover, and files in folders Lens scans stay as they are.
          </li>
          <li>Your passkey needs the PRF extension: recent phones, computers and security keys have it.</li>
        </ul>
      </Dialog>
      <Dialog
        open={confirm === "unseal"}
        onOpenChange={(o) => !o && setConfirm(null)}
        title={`Make ${ns} an ordinary namespace?`}
        description="The server keeps a key that opens it again, so background work runs any time. Its files stay encrypted."
        actions={
          <>
            <Button variant="ghost" onClick={() => setConfirm(null)}>
              Cancel
            </Button>
            <Button variant="primary" disabled={busy} onClick={() => unseal.mutate()}>
              Make it ordinary
            </Button>
          </>
        }
      />
    </section>
  );
}
