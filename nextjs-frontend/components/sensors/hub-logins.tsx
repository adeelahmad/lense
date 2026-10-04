"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import { Sensors } from "@/app/openapi-client";
import type { HubLogin } from "@/app/openapi-client/types.gen";
import { useLogins, useSensors } from "@/components/sensors/data";
import { NamespaceSelect } from "@/components/sensors/parts";
import { SensorsHeader } from "@/components/sensors/sensors-page";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input } from "@/components/ui/field";
import { DateTime, EmptyState, SkeletonRows } from "@/components/ui/states";
import { Table, Td, Th, THead, Tr } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { useArchive } from "@/lib/hooks/session";

/** A password a person needn't make up: 20 letters and digits from the browser's random source. */
export function randomPassword(n = 20): string {
  const abc = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789";
  const bytes = new Uint8Array(n);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => abc[b % abc.length]).join("");
}

function AddLoginDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState(() => randomPassword());
  const [space, setSpace] = useState<number | null>(null);
  const create = useMutation({
    mutationFn: () => data(Sensors.createLogin({ client, body: { username: username.trim(), password, space } })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["sensor-logins"] });
      toast({ tone: "green", title: "Login added", body: username });
      onOpenChange(false);
      setUsername("");
      setPassword(randomPassword());
      setSpace(null);
    },
  });
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Add a hub login"
      description="Devices sign in to the MQTT hub with a username and password. Copy the password now: it isn’t shown again."
      actions={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={!username.trim() || password.length < 8 || create.isPending}
            onClick={() => create.mutate()}
          >
            Add login
          </Button>
        </>
      }
    >
      <Field label="Username" hint="Letters, digits and . _ @ + -">
        {({ id, describedBy }) => (
          <Input
            id={id}
            aria-describedby={describedBy}
            mono
            autoComplete="off"
            value={username}
            placeholder="zigbee2mqtt"
            onChange={(e) => setUsername(e.target.value)}
          />
        )}
      </Field>
      <Field label="Password" hint="Made for you; at least 8 characters">
        {({ id, describedBy }) => (
          <div className="flex gap-2">
            <Input
              id={id}
              aria-describedby={describedBy}
              mono
              autoComplete="off"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <Button size="sm" className="h-10" onClick={() => void navigator.clipboard?.writeText(password)}>
              Copy
            </Button>
          </div>
        )}
      </Field>
      <Field label="Namespace" hint="New devices that sign in with it start there">
        {({ id }) => <NamespaceSelect id={id} value={space} onChange={setSpace} />}
      </Field>
      {create.isError && <Banner tone="error">{create.error.message}</Banner>}
    </Dialog>
  );
}

/** The usernames devices sign in to the MQTT hub with. */
export function HubLoginsPage() {
  const { admin, me } = useArchive();
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const logins = useLogins();
  const sensors = useSensors();
  const [adding, setAdding] = useState(false);
  const [deleting, setDeleting] = useState<HubLogin | null>(null);
  const del = useMutation({
    mutationFn: (l: HubLogin) => data(Sensors.deleteLogin({ client, path: { lid: l.id } })),
    onSuccess: (_, l) => {
      void qc.invalidateQueries({ queryKey: ["sensor-logins"] });
      toast({ title: "Login removed", body: l.username });
      setDeleting(null);
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t remove the login", body: e.message }),
  });
  if (me && !admin)
    return (
      <EmptyState icon={<KeyRound />} title="Hub logins are for admins">
        Admins decide which devices may send to Lens.
      </EmptyState>
    );
  const list = logins.data ?? [];
  const hub = sensors.data?.hub;
  const newCount = (sensors.data?.sensors ?? []).filter((s) => s.status === "new").length;
  return (
    <div className="flex flex-col gap-4 px-4 pb-10 pt-[18px] md:px-6">
      <SensorsHeader
        tab="logins"
        newCount={newCount}
        actions={
          <Button size="sm" variant="primary" icon={<Plus />} onClick={() => setAdding(true)}>
            Add login
          </Button>
        }
      />
      {hub && hub.enabled === false && (
        <Banner>The hub is off; these logins work once it’s on (Settings → Sensors).</Banner>
      )}
      {logins.isLoading || !me ? (
        <SkeletonRows rows={3} />
      ) : logins.error ? (
        <EmptyState
          tone="error"
          icon={<KeyRound />}
          title="Couldn’t load logins"
          actions={<Button onClick={() => logins.refetch()}>Try again</Button>}
        >
          {(logins.error as Error).message}
        </EmptyState>
      ) : !list.length ? (
        <EmptyState
          icon={<KeyRound />}
          title="No logins yet"
          actions={
            <Button variant="primary" icon={<Plus />} onClick={() => setAdding(true)}>
              Add login
            </Button>
          }
        >
          MQTT devices need a username and password to send to the hub, unless Settings → Sensors lets them in without
          one. Syslog senders are let in by their network instead.
        </EmptyState>
      ) : (
        <div className="overflow-hidden rounded-md border border-border">
          <Table aria-label="Hub logins">
            <THead className="border-t-0">
              <tr>
                <Th>Username</Th>
                <Th>Namespace</Th>
                <Th>Last signed in</Th>
                <Th>Added</Th>
                <Th>
                  <span className="sr-only">Actions</span>
                </Th>
              </tr>
            </THead>
            <tbody>
              {list.map((l) => (
                <Tr key={l.id}>
                  <Td className="font-mono text-[13px] font-semibold">{l.username}</Td>
                  <Td className="text-[13px] text-fg-secondary">{l.namespace ?? "—"}</Td>
                  <Td className="text-[13px] text-fg-secondary">
                    <DateTime iso={l.last_seen_at} />
                  </Td>
                  <Td className="text-[13px] text-fg-secondary">
                    <DateTime iso={l.created_at} />
                  </Td>
                  <Td className="text-right">
                    <Button
                      size="xs"
                      variant="danger-ghost"
                      icon={<Trash2 />}
                      aria-label={`Remove ${l.username}`}
                      onClick={() => setDeleting(l)}
                    >
                      Remove
                    </Button>
                  </Td>
                </Tr>
              ))}
            </tbody>
          </Table>
        </div>
      )}
      <AddLoginDialog open={adding} onOpenChange={setAdding} />
      <Dialog
        open={Boolean(deleting)}
        onOpenChange={(o) => !o && setDeleting(null)}
        title={`Remove “${deleting?.username ?? ""}”?`}
        description="Devices using it can’t sign in again. Those connected now stay connected until they reconnect."
        actions={
          <>
            <Button variant="ghost" onClick={() => setDeleting(null)}>
              Cancel
            </Button>
            <Button variant="danger" disabled={del.isPending} onClick={() => deleting && del.mutate(deleting)}>
              Remove login
            </Button>
          </>
        }
      />
    </div>
  );
}
