"use client";

import { Bell, Plus, Send } from "lucide-react";
import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";

import type { NotifyTarget, NotifyTargetCreate } from "@/app/openapi-client/types.gen";
import {
  useCreateTarget,
  useDeleteTarget,
  useDeliveries,
  useNotifyTargets,
  useRotateSecret,
  useTestTarget,
  useUpdateTarget,
} from "@/components/notifications/hooks";
import { isUnreachable } from "@/components/errors/error-states";
import { ChoiceCards } from "@/components/settings/controls";
import { Badge } from "@/components/ui/badge";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Checkbox, Field, Input, Switch } from "@/components/ui/field";
import { CodeBlock, EmptyState, SkeletonRows } from "@/components/ui/states";
import { relative } from "@/lib/format";
import { needRole } from "@/lib/hooks/session";

type Kind = NotifyTarget["kind"];
type EventType = NotifyTarget["events"][number];

const KINDS: { value: Kind; label: string; hint: string }[] = [
  { value: "matterbridge", label: "Matterbridge", hint: "to Slack, Discord, Matrix, Telegram… through a gateway" },
  { value: "webhook", label: "Webhook", hint: "signed JSON, for scripts, n8n, Home Assistant" },
  { value: "slack", label: "Slack-style", hint: "Slack, Mattermost or Rocket.Chat incoming webhook" },
  { value: "discord", label: "Discord", hint: "a channel’s webhook" },
];
const KIND_LABEL = Object.fromEntries(KINDS.map((k) => [k.value, k.label])) as Record<Kind, string>;
const URL_HINT: Record<Kind, string> = {
  matterbridge: "The gateway’s API address, like http://matterbridge:4242",
  webhook: "Where Lens POSTs each event as JSON",
  slack: "The incoming webhook’s URL",
  discord: "The channel webhook’s URL, from its Integrations settings",
};
const DEFAULT_EVENTS: EventType[] = ["job.failed", "batch.finished"];

/**
 * A namespace's notifications (docs/notifications.md), on its page: owners send what happens in it (runs finishing or
 * failing, batch runs finishing, things added) to webhooks and through Matterbridge to chat rooms.
 */
export function NotificationsSection({ ns, isOwner, admin }: { ns: string; isOwner: boolean; admin: boolean }) {
  const q = useNotifyTargets(ns, isOwner);
  const remove = useDeleteTarget(ns);
  const test = useTestTarget(ns);
  const [editing, setEditing] = useState<NotifyTarget | "new" | null>(null);
  const [deleting, setDeleting] = useState<NotifyTarget | null>(null);
  const [log, setLog] = useState<NotifyTarget | null>(null);
  const [secret, setSecret] = useState<string | null>(null);
  const labels = Object.fromEntries((q.data?.events ?? []).map((e) => [e.type, e.label])) as Record<string, string>;
  return (
    <section
      id="notifications"
      aria-labelledby="notifications-heading"
      className="flex scroll-mt-4 flex-col gap-3.5 rounded-md border border-border bg-background px-4 py-5 sm:px-6"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 flex-1 basis-[260px] flex-col gap-1">
          <h2 id="notifications-heading" className="text-[17px] font-bold leading-tight text-fg">
            Notifications
          </h2>
          <p className="text-[13px] leading-[1.45] text-fg-secondary">
            Tell a chat room or another app when something happens in {ns}: a run finishes or fails, a batch run
            finishes, something is added.
          </p>
        </div>
        <Button
          size="sm"
          icon={<Plus />}
          disabled={!isOwner}
          disabledReason={needRole("owner", ns)}
          onClick={() => setEditing("new")}
        >
          New target
        </Button>
      </div>
      {!isOwner ? null : q.isPending ? (
        <SkeletonRows rows={2} />
      ) : q.isError ? (
        <EmptyState
          tone="error"
          icon={<Bell />}
          title={isUnreachable(q.error) ? "Can’t reach the server" : "Couldn’t load the notifications"}
          actions={<Button onClick={() => q.refetch()}>Try again</Button>}
        >
          {q.error.message}
        </EmptyState>
      ) : (
        <>
          {!q.data.enabled && (
            <Banner tone="warning" title="Notifications are off for the server.">
              Nothing is sent until{" "}
              {admin ? (
                <>
                  you turn them on in{" "}
                  <Link href="/settings/notifications" className="font-semibold text-fg-accent hover:underline">
                    Settings
                  </Link>
                </>
              ) : (
                "an admin turns them on in Settings"
              )}
              .
            </Banner>
          )}
          {q.data.targets.length ? (
            <ul aria-label="Notification targets" className="flex flex-col">
              {q.data.targets.map((t) => (
                <li key={t.id} className="flex flex-wrap items-start gap-x-3 gap-y-2 border-t border-border py-2.5">
                  <span className="grid size-8 shrink-0 place-items-center rounded-sm bg-surface-neutral text-fg-secondary">
                    <Bell aria-hidden className="size-4" />
                  </span>
                  <span className="flex min-w-0 flex-1 basis-[220px] flex-col gap-0.5">
                    <span className="flex flex-wrap items-center gap-2">
                      <b className="text-[13.5px] font-semibold text-fg">{t.name}</b>
                      <Badge>{KIND_LABEL[t.kind]}</Badge>
                      {!t.enabled && <Badge tone="gate">Off</Badge>}
                    </span>
                    <span className="break-all font-mono text-[12px] text-fg-secondary">
                      {t.url}
                      {t.kind === "matterbridge" && t.gateway ? ` · gateway ${t.gateway}` : ""}
                    </span>
                    <span className="text-[12px] text-fg-muted">{t.events.map((e) => labels[e] ?? e).join(" · ")}</span>
                    {t.last && (
                      <span className={t.last.ok ? "text-[12px] text-green-dark" : "text-[12px] text-red-dark"}>
                        {t.last.ok
                          ? `Last sent ${relative(t.last.at)}`
                          : `Last send failed ${relative(t.last.at)}: ${t.last.error ?? `HTTP ${t.last.code}`}`}
                      </span>
                    )}
                  </span>
                  <span className="flex flex-wrap gap-1.5">
                    <Button
                      size="xs"
                      variant="ghost"
                      icon={<Send />}
                      disabled={test.isPending}
                      onClick={() => test.mutate(t)}
                      aria-label={`Send ${t.name} a test`}
                    >
                      Test
                    </Button>
                    <Button size="xs" variant="ghost" onClick={() => setLog(t)} aria-label={`What ${t.name} was sent`}>
                      Sent
                    </Button>
                    <Button size="xs" variant="ghost" onClick={() => setEditing(t)} aria-label={`Edit ${t.name}`}>
                      Edit
                    </Button>
                    <Button
                      size="xs"
                      variant="danger-ghost"
                      onClick={() => setDeleting(t)}
                      aria-label={`Delete ${t.name}`}
                    >
                      Delete
                    </Button>
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="border-t border-border pt-3 text-[13px] text-fg-muted">No targets yet.</p>
          )}
        </>
      )}
      <TargetDialog
        ns={ns}
        target={editing}
        events={q.data?.events ?? []}
        admin={admin}
        onClose={() => setEditing(null)}
        onSecret={setSecret}
      />
      <DeliveriesDialog ns={ns} target={log} onClose={() => setLog(null)} />
      <Dialog
        open={Boolean(secret)}
        onOpenChange={(o) => !o && setSecret(null)}
        title="Copy the signing secret now"
        description="This is the only time it’s shown. The receiver checks each delivery’s webhook-signature header with it (Standard Webhooks); if you lose it, make a new one from Edit."
        actions={
          <Button variant="primary" onClick={() => setSecret(null)}>
            I’ve saved it
          </Button>
        }
      >
        {secret && <CodeBlock text={secret} label="the secret" />}
      </Dialog>
      <Dialog
        open={Boolean(deleting)}
        onOpenChange={(o) => !o && setDeleting(null)}
        title={`Delete ${deleting?.name ?? "this target"}?`}
        description="It gets no more notifications, and what it was sent is forgotten."
        actions={
          <>
            <Button variant="ghost" onClick={() => setDeleting(null)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              disabled={remove.isPending}
              onClick={() => deleting && remove.mutate(deleting, { onSuccess: () => setDeleting(null) })}
            >
              {remove.isPending ? "Deleting…" : "Delete"}
            </Button>
          </>
        }
      />
    </section>
  );
}

/** Add a target, or change one: where it is, what it's sent, and for Matterbridge its gateway, name and token. */
function TargetDialog({
  ns,
  target,
  events,
  admin,
  onClose,
  onSecret,
}: {
  ns: string;
  target: NotifyTarget | "new" | null;
  events: { type: EventType; label: string }[];
  admin: boolean;
  onClose: () => void;
  onSecret: (s: string) => void;
}) {
  const create = useCreateTarget(ns);
  const update = useUpdateTarget(ns);
  const rotate = useRotateSecret(ns);
  const editing = target && target !== "new" ? target : null;
  const [kind, setKind] = useState<Kind>("matterbridge");
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [chosen, setChosen] = useState<EventType[]>(DEFAULT_EVENTS);
  const [enabled, setEnabled] = useState(true);
  const [gateway, setGateway] = useState("");
  const [username, setUsername] = useState("");
  const [token, setToken] = useState("");
  const [dropToken, setDropToken] = useState(false);
  const save = editing ? update : create;
  const { reset: resetCreate } = create;
  const { reset: resetUpdate } = update;
  useEffect(() => {
    if (!target) return;
    const t = target === "new" ? null : target;
    setKind(t?.kind ?? "matterbridge");
    setName(t?.name ?? "");
    setUrl("");
    setChosen(t?.events ?? DEFAULT_EVENTS);
    setEnabled(t?.enabled ?? true);
    setGateway(t?.gateway ?? "");
    setUsername(t?.username ?? "");
    setToken("");
    setDropToken(false);
    resetCreate();
    resetUpdate();
  }, [target, resetCreate, resetUpdate]);
  const missing = !name.trim()
    ? "Give it a name"
    : !editing && !url.trim()
      ? "Give its address"
      : kind === "matterbridge" && !gateway.trim()
        ? "Give the gateway to send to"
        : !chosen.length
          ? "Choose at least one event"
          : null;
  const toggle = (e: EventType, on: boolean) =>
    setChosen((c) => (on ? [...c, e] : c.filter((x) => x !== e)).filter((x, i, a) => a.indexOf(x) === i));
  const submit = (ev: FormEvent) => {
    ev.preventDefault();
    if (missing) return;
    const mb = kind === "matterbridge";
    if (editing) {
      update.mutate(
        {
          id: editing.id,
          body: {
            name: name.trim(),
            events: chosen,
            enabled,
            ...(url.trim() ? { url: url.trim() } : {}),
            ...(mb ? { gateway: gateway.trim(), username: username.trim() } : {}),
            ...(mb && (token.trim() || dropToken) ? { token: dropToken ? "" : token.trim() } : {}),
          },
        },
        { onSuccess: onClose },
      );
    } else {
      const body: NotifyTargetCreate = {
        name: name.trim(),
        kind,
        url: url.trim(),
        events: chosen,
        enabled,
        ...(mb ? { gateway: gateway.trim(), username: username.trim() || null, token: token.trim() || null } : {}),
      };
      create.mutate(body, {
        onSuccess: (r) => {
          onClose();
          if (r.secret) onSecret(r.secret);
        },
      });
    }
  };
  return (
    <Dialog
      open={Boolean(target)}
      onOpenChange={(o) => !o && onClose()}
      title={editing ? `Edit ${editing.name}` : "New notification target"}
      description={
        <>
          Targets must be public addresses unless{" "}
          {admin ? (
            <Link href="/settings/notifications" className="font-semibold text-fg-accent hover:underline">
              you allow a private network
            </Link>
          ) : (
            "an admin allows a private network"
          )}
          , which a Matterbridge next to Lens needs.
        </>
      }
      wide
    >
      <form onSubmit={submit} className="flex flex-col gap-4">
        {!editing && (
          <div className="flex flex-col gap-2">
            <span className="text-[13px] font-bold text-fg-strong">Send to</span>
            <ChoiceCards
              label="Send to"
              size="sm"
              columns={2}
              value={kind}
              onChange={(v) => setKind(v as Kind)}
              options={KINDS}
            />
          </div>
        )}
        <Field label="Name" hint="For you, e.g. Team chat">
          {(f) => (
            <Input
              id={f.id}
              aria-describedby={f.describedBy}
              value={name}
              maxLength={80}
              onChange={(e) => setName(e.target.value)}
            />
          )}
        </Field>
        <Field
          label="Address"
          hint={editing ? `Leave empty to keep ${editing.url ?? "the one saved"}` : URL_HINT[kind]}
        >
          {(f) => (
            <Input
              id={f.id}
              aria-describedby={f.describedBy}
              value={url}
              maxLength={2000}
              inputMode="url"
              spellCheck={false}
              className="font-mono"
              placeholder={kind === "matterbridge" ? "http://matterbridge:4242" : "https://"}
              onChange={(e) => setUrl(e.target.value)}
            />
          )}
        </Field>
        {kind === "matterbridge" && (
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Gateway" hint="The [[gateway]] name in matterbridge.toml">
              {(f) => (
                <Input
                  id={f.id}
                  aria-describedby={f.describedBy}
                  value={gateway}
                  maxLength={100}
                  className="font-mono"
                  onChange={(e) => setGateway(e.target.value)}
                />
              )}
            </Field>
            <Field label="Send as" hint="The name messages show; Lens when empty" optional>
              {(f) => (
                <Input
                  id={f.id}
                  aria-describedby={f.describedBy}
                  value={username}
                  maxLength={60}
                  onChange={(e) => setUsername(e.target.value)}
                />
              )}
            </Field>
            <Field
              label="API token"
              hint={editing?.token_set ? "Set; leave empty to keep it" : "The [api] section’s Token, if it has one"}
              optional
              className="sm:col-span-2"
            >
              {(f) => (
                <Input
                  id={f.id}
                  aria-describedby={f.describedBy}
                  type="password"
                  autoComplete="off"
                  value={token}
                  maxLength={500}
                  disabled={dropToken}
                  onChange={(e) => setToken(e.target.value)}
                />
              )}
            </Field>
            {editing?.token_set && (
              <Checkbox checked={dropToken} onCheckedChange={setDropToken} label="Remove the token" />
            )}
          </div>
        )}
        <div className="flex flex-col gap-2">
          <span className="text-[13px] font-bold text-fg-strong">Events</span>
          {events.map((e) => (
            <Checkbox
              key={e.type}
              checked={chosen.includes(e.type)}
              onCheckedChange={(on) => toggle(e.type, on)}
              label={
                <span className="flex flex-col">
                  <span className="text-[13.5px] text-fg">{e.label}</span>
                  <span className="font-mono text-[11.5px] text-fg-muted">{e.type}</span>
                </span>
              }
            />
          ))}
          <p className="text-[12px] leading-[1.45] text-fg-muted">
            A batch run’s runs are told as one message when it finishes, and many things added at once as one.
          </p>
        </div>
        <Switch checked={enabled} onCheckedChange={setEnabled} label="On" />
        {editing?.kind === "webhook" && (
          <div className="flex flex-wrap items-center gap-2 rounded-md bg-surface px-3 py-2.5 text-[12.5px] text-fg-secondary">
            <span className="min-w-0 flex-1">Deliveries are signed with a secret only the receiver knows.</span>
            <Button
              size="xs"
              disabled={rotate.isPending}
              onClick={() =>
                rotate.mutate(editing, {
                  onSuccess: (r) => {
                    onClose();
                    onSecret(r.secret);
                  },
                })
              }
            >
              New signing secret
            </Button>
          </div>
        )}
        {save.isError && <Banner tone="error">{save.error.message}</Banner>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="submit"
            variant="primary"
            disabled={Boolean(missing) || save.isPending}
            disabledReason={missing ?? undefined}
          >
            {save.isPending ? "Saving…" : editing ? "Save" : "Add target"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

const STATUS: Record<string, { label: string; tone: "green" | "red" | "neutral" | "intent" }> = {
  sent: { label: "Sent", tone: "green" },
  failed: { label: "Failed", tone: "red" },
  dropped: { label: "Not sent", tone: "neutral" },
  pending: { label: "Waiting", tone: "intent" },
  sending: { label: "Sending", tone: "intent" },
};

/** What a target was sent lately, with how each went. */
function DeliveriesDialog({ ns, target, onClose }: { ns: string; target: NotifyTarget | null; onClose: () => void }) {
  const q = useDeliveries(ns, target?.id ?? null);
  return (
    <Dialog
      open={Boolean(target)}
      onOpenChange={(o) => !o && onClose()}
      title={`What ${target?.name ?? "it"} was sent`}
      description="The latest 50, kept for 30 days. A failed send is tried again, waiting longer each time."
      wide
    >
      {q.isPending ? (
        <SkeletonRows rows={3} />
      ) : q.isError ? (
        <Banner tone="error">{q.error.message}</Banner>
      ) : q.data.length ? (
        <ul aria-label="Deliveries" className="flex flex-col">
          {q.data.map((d) => {
            const s = STATUS[d.status] ?? STATUS.pending;
            return (
              <li key={d.id} className="flex flex-col gap-1 border-t border-border py-2.5 first:border-t-0">
                <span className="flex flex-wrap items-center gap-2 text-[12px] text-fg-muted">
                  <Badge tone={s.tone}>{s.label}</Badge>
                  <span className="font-mono">{d.event}</span>
                  <span>{relative(d.created_at)}</span>
                  {(d.attempts ?? 0) > 1 && <span>{d.attempts} tries</span>}
                  {d.status === "pending" && d.next_at && <span>next {relative(d.next_at)}</span>}
                </span>
                {d.text && <span className="text-[13px] leading-[1.45] text-fg">{d.text}</span>}
                {d.error && <span className="break-words text-[12px] text-red-dark">{d.error}</span>}
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="text-[13px] text-fg-muted">Nothing yet. Send a test to see one here.</p>
      )}
    </Dialog>
  );
}
