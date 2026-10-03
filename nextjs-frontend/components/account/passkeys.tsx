"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fingerprint, Pencil, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import { Auth, type Passkey } from "@/app/openapi-client";
import { Banner } from "@/components/ui/banner";
import { Button, IconButton } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { Panel } from "@/components/ui/panel";
import { DateTime, Skeleton } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { createPasskey, deviceName, passkeyErrorMessage, passkeysUnavailableReason } from "@/lib/auth/webauthn";

/** Your passkeys: add one on this device (or a phone), rename them, remove ones you no longer use (never the last). */
export function PasskeysPanel() {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const [error, setError] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState<string | null>(null);
  useEffect(() => setUnavailable(passkeysUnavailableReason()), []);
  const list = useQuery({ queryKey: ["passkeys"], queryFn: () => data(Auth.listPasskeys({ client })) });
  const refresh = () => void qc.invalidateQueries({ queryKey: ["passkeys"] });

  const add = useMutation({
    mutationFn: async () => {
      const start = await data(Auth.addPasskeyOptions({ client }));
      const credential = await createPasskey(start.options);
      return data(Auth.addPasskey({ client, body: { flow: start.flow, credential, name: deviceName() } }));
    },
    onSuccess: (p) => {
      setError(null);
      refresh();
      toast({ title: "Passkey added", body: `${p.name} signs you in now.`, tone: "green" });
    },
    onError: (e) => setError(passkeyErrorMessage(e, true)),
  });
  const remove = useMutation({
    mutationFn: (id: string) => data(Auth.removePasskey({ client, path: { pid: id } })),
    onSuccess: () => {
      setError(null);
      refresh();
    },
    onError: (e) => setError(e.message),
  });

  return (
    <Panel
      title="Passkeys"
      subtitle="Sign in with your fingerprint, face or device PIN. A passkey works on the address it was made at."
      actions={
        <Button
          size="sm"
          icon={<Fingerprint />}
          onClick={() => add.mutate()}
          disabled={add.isPending || Boolean(unavailable)}
          disabledReason={unavailable ?? undefined}
        >
          {add.isPending ? "Waiting for your passkey…" : "Add a passkey"}
        </Button>
      }
    >
      <div className="flex flex-col gap-3">
        {error && <Banner tone="error">{error}</Banner>}
        {list.isLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : list.data?.length ? (
          <ul className="flex flex-col divide-y divide-border rounded-sm border border-border">
            {list.data.map((p) => (
              <PasskeyRow
                key={p.id}
                passkey={p}
                last={list.data.length === 1}
                onRemove={() => remove.mutate(p.id)}
                onRenamed={refresh}
              />
            ))}
          </ul>
        ) : (
          <p className="text-[13px] text-fg-secondary">
            No passkeys yet. Add one so you can sign in without a password.
          </p>
        )}
      </div>
    </Panel>
  );
}

function PasskeyRow({
  passkey: p,
  last,
  onRemove,
  onRenamed,
}: {
  passkey: Passkey;
  last: boolean;
  onRemove: () => void;
  onRenamed: () => void;
}) {
  const client = useApiClient();
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [name, setName] = useState(p.name);
  const rename = useMutation({
    mutationFn: () => data(Auth.renamePasskey({ client, path: { pid: p.id }, body: { name: name.trim() } })),
    onSuccess: () => {
      setEditing(false);
      onRenamed();
    },
  });
  return (
    <li className="flex flex-wrap items-center gap-3 px-3 py-2.5 text-[13.5px]">
      <Fingerprint className="size-4 text-fg-muted" aria-hidden />
      <div className="min-w-0 flex-1">
        {editing ? (
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (name.trim()) rename.mutate();
            }}
          >
            <Input
              aria-label="Passkey name"
              value={name}
              maxLength={60}
              autoFocus
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => e.key === "Escape" && setEditing(false)}
              className="h-8 max-w-[260px]"
            />
            <Button type="submit" size="sm" disabled={!name.trim() || rename.isPending}>
              Save
            </Button>
          </form>
        ) : (
          <div className="font-medium text-fg">
            {p.name}
            {p.backed_up && <span className="ml-2 text-[12px] font-normal text-fg-muted">synced</span>}
          </div>
        )}
        <div className="text-[12px] text-fg-muted">
          {p.rp_id} · added <DateTime iso={p.created_at} />
          {p.last_used_at ? (
            <>
              {" "}
              · last used <DateTime iso={p.last_used_at} />
            </>
          ) : null}
        </div>
      </div>
      {!editing && (
        <IconButton label="Rename" onClick={() => setEditing(true)}>
          <Pencil />
        </IconButton>
      )}
      {confirming ? (
        <span className="flex items-center gap-1.5">
          <Button size="sm" variant="danger" onClick={onRemove}>
            Remove
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>
            Keep
          </Button>
        </span>
      ) : (
        <IconButton
          label={last ? "Your last passkey: add another before removing it" : "Remove"}
          disabled={last}
          onClick={() => setConfirming(true)}
        >
          <Trash2 />
        </IconButton>
      )}
    </li>
  );
}
