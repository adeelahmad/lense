"use client";

import { Network, Plus } from "lucide-react";
import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";

import type { IpGroup, RecordingIpGroup } from "@/app/openapi-client/types.gen";
import {
  useChooseIpGroup,
  useDeleteIpGroup,
  useIpGroups,
  useRecordingIpGroups,
  useSaveIpGroup,
} from "@/components/access/hooks";
import { ipGroupOpens, rangesFromText, rangesText } from "@/components/access/model";
import { isUnreachable } from "@/components/errors/error-states";
import { ChoiceCards } from "@/components/settings/controls";
import { Badge } from "@/components/ui/badge";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Checkbox, Field, Input, Textarea } from "@/components/ui/field";
import { EmptyState, Skeleton, SkeletonRows } from "@/components/ui/states";
import { Tooltip } from "@/components/ui/tooltip";
import { needRole } from "@/lib/hooks/session";

const nsPage = (ns: string) => `/admin/namespaces/${encodeURIComponent(ns)}#ip-groups`;

/**
 * A namespace's IP groups (docs/access.md), on its page: visitors from their addresses see all of its recordings, or
 * the ones chosen for them, without signing in. Owners add, change and delete them.
 */
export function IpGroupsSection({ ns, isOwner, admin }: { ns: string; isOwner: boolean; admin: boolean }) {
  const q = useIpGroups(ns, isOwner);
  const remove = useDeleteIpGroup(ns);
  const [editing, setEditing] = useState<IpGroup | "new" | null>(null);
  const [deleting, setDeleting] = useState<IpGroup | null>(null);
  return (
    <section
      id="ip-groups"
      aria-labelledby="ip-groups-heading"
      className="flex scroll-mt-4 flex-col gap-3.5 rounded-md border border-border bg-background px-4 py-5 sm:px-6"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 flex-1 basis-[260px] flex-col gap-1">
          <h2 id="ip-groups-heading" className="text-[17px] font-bold leading-tight text-fg">
            IP groups
          </h2>
          <p className="text-[13px] leading-[1.45] text-fg-secondary">
            Visitors from these addresses, in a reading room or on a campus network, see all of {ns}’s recordings or the
            ones chosen for them, without signing in: on the pages visitors see and in IIIF.
          </p>
        </div>
        <Button
          size="sm"
          icon={<Plus />}
          disabled={!isOwner}
          disabledReason={needRole("owner", ns)}
          onClick={() => setEditing("new")}
        >
          New IP group
        </Button>
      </div>
      {!isOwner ? null : q.isPending ? (
        <SkeletonRows rows={2} />
      ) : q.isError ? (
        <EmptyState
          tone="error"
          icon={<Network />}
          title={isUnreachable(q.error) ? "Can’t reach the server" : "Couldn’t load the IP groups"}
          actions={<Button onClick={() => q.refetch()}>Try again</Button>}
        >
          {q.error.message}
        </EmptyState>
      ) : (
        <>
          {q.data.address ? (
            <p className="rounded-md bg-surface px-3 py-2.5 text-[12.5px] leading-[1.45] text-fg-secondary">
              The server sees your address as <code className="font-mono text-fg">{q.data.address}</code>. A group that
              holds it says so.
            </p>
          ) : (
            <Banner tone="warning" title="The server can’t tell visitors’ addresses yet.">
              IP groups match nobody until{" "}
              {admin ? (
                <>
                  you list the web app’s address under{" "}
                  <Link href="/settings/access" className="font-semibold text-fg-accent hover:underline">
                    Trusted proxies
                  </Link>
                </>
              ) : (
                "an admin lists the web app’s address under Trusted proxies in Settings"
              )}
              .
            </Banner>
          )}
          {q.data.groups.length ? (
            <ul aria-label="IP groups" className="flex flex-col">
              {q.data.groups.map((g) => (
                <li key={g.id} className="flex flex-wrap items-start gap-x-3 gap-y-2 border-t border-border py-2.5">
                  <span className="grid size-8 shrink-0 place-items-center rounded-sm bg-surface-neutral text-fg-secondary">
                    <Network aria-hidden className="size-4" />
                  </span>
                  <span className="flex min-w-0 flex-1 basis-[200px] flex-col gap-0.5">
                    <span className="flex flex-wrap items-center gap-2">
                      <b className="text-[13.5px] font-semibold text-fg">{g.name}</b>
                      {g.here && <Badge tone="green">You’re on it</Badge>}
                    </span>
                    <span className="break-all font-mono text-[12px] text-fg-secondary">{rangesText(g.ranges)}</span>
                    <span className="text-[12px] text-fg-muted">{ipGroupOpens(g, ns)}</span>
                  </span>
                  <span className="flex gap-1.5">
                    <Button size="xs" variant="ghost" onClick={() => setEditing(g)} aria-label={`Edit ${g.name}`}>
                      Edit
                    </Button>
                    <Button
                      size="xs"
                      variant="danger-ghost"
                      onClick={() => setDeleting(g)}
                      aria-label={`Delete ${g.name}`}
                    >
                      Delete
                    </Button>
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="border-t border-border pt-3 text-[13px] text-fg-muted">No IP groups yet.</p>
          )}
        </>
      )}
      <IpGroupDialog ns={ns} group={editing} onClose={() => setEditing(null)} />
      <Dialog
        open={Boolean(deleting)}
        onOpenChange={(o) => !o && setDeleting(null)}
        title={`Delete ${deleting?.name ?? "this IP group"}?`}
        description={
          deleting
            ? `Visitors from its addresses lose what it opens: ${ipGroupOpens(deleting, ns).toLowerCase()}.`
            : undefined
        }
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

/** Add an IP group, or change one: its name, addresses and what it opens. */
function IpGroupDialog({ ns, group, onClose }: { ns: string; group: IpGroup | "new" | null; onClose: () => void }) {
  const save = useSaveIpGroup(ns);
  const editing = group && group !== "new" ? group : null;
  const [name, setName] = useState("");
  const [text, setText] = useState("");
  const [everything, setEverything] = useState(false);
  const { reset } = save;
  useEffect(() => {
    if (!group) return;
    const g = group === "new" ? null : group;
    setName(g?.name ?? "");
    setText((g?.ranges ?? []).join("\n"));
    setEverything(g?.everything ?? false);
    reset();
  }, [group, reset]);
  const ranges = rangesFromText(text);
  const missing = !name.trim() ? "Give it a name" : !ranges.length ? "Add an address or a range" : null;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (missing) return;
    save.mutate({ id: editing?.id, body: { name: name.trim(), ranges, everything } }, { onSuccess: onClose });
  };
  return (
    <Dialog
      open={Boolean(group)}
      onOpenChange={(o) => !o && onClose()}
      title={editing ? `Edit ${editing.name}` : "New IP group"}
      description="Visitors from these addresses see all of what it opens without signing in."
    >
      <form onSubmit={submit} className="flex flex-col gap-4">
        <Field label="Name" hint="Visitors see it, e.g. Reading room">
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
        <Field label="Addresses" hint="One per line: an address (198.51.100.7) or a range (198.51.100.0/24)">
          {(f) => (
            <Textarea
              id={f.id}
              aria-describedby={f.describedBy}
              mono
              rows={4}
              value={text}
              onChange={(e) => setText(e.target.value)}
              spellCheck={false}
            />
          )}
        </Field>
        <div className="flex flex-col gap-2">
          <span className="text-[13px] font-bold text-fg-strong">Opens</span>
          <ChoiceCards
            label="Opens"
            size="sm"
            value={everything ? "all" : "chosen"}
            onChange={(v) => setEverything(v === "all")}
            options={[
              { value: "all", label: "Every recording", hint: `in ${ns}, now and later` },
              { value: "chosen", label: "Chosen recordings", hint: "in each one’s Access dialog" },
            ]}
          />
        </div>
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
            {save.isPending ? "Saving…" : editing ? "Save" : "Add IP group"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

/** In a recording's Access dialog (owners): the namespace's IP groups, and which of them open this recording. */
export function RecordingIpGroups({ rid, ns }: { rid: number; ns: string }) {
  const q = useRecordingIpGroups(rid);
  const choose = useChooseIpGroup(rid, ns);
  const box = (g: RecordingIpGroup) => (
    <Checkbox
      checked={g.opens}
      disabled={g.everything || choose.isPending}
      onCheckedChange={(on) => choose.mutate({ g, on })}
      label={
        <span className="flex min-w-0 flex-col">
          <span className="text-[13.5px] font-semibold text-fg">{g.name}</span>
          <span className="break-all font-mono text-[12px] text-fg-muted">{rangesText(g.ranges)}</span>
        </span>
      }
    />
  );
  return (
    <section
      aria-labelledby="recording-ip-groups-heading"
      className="flex flex-col gap-2.5 rounded-md border border-border px-3 py-3"
    >
      <div className="flex flex-col gap-0.5">
        <h3 id="recording-ip-groups-heading" className="text-[13px] font-bold text-fg-strong">
          IP groups
        </h3>
        <p className="text-[12.5px] leading-[1.45] text-fg-secondary">
          Visitors from their addresses see all of it without signing in, on the pages visitors see and in IIIF.
        </p>
      </div>
      {q.isPending ? (
        <Skeleton className="h-10 w-full" />
      ) : q.isError ? (
        <p className="text-[12.5px] text-red-dark">{q.error.message}</p>
      ) : q.data.length ? (
        <>
          <ul aria-label="IP groups" className="flex flex-col">
            {q.data.map((g) => (
              <li key={g.id} className="border-t border-border py-2 first:border-t-0">
                {g.everything ? (
                  <Tooltip content={`${g.name} opens every recording in ${ns}; change that on the namespace’s page`}>
                    <span className="block w-fit">{box(g)}</span>
                  </Tooltip>
                ) : (
                  box(g)
                )}
              </li>
            ))}
          </ul>
          <Link href={nsPage(ns)} className="w-fit text-[12.5px] font-semibold text-fg-accent hover:underline">
            Manage {ns}’s IP groups
          </Link>
        </>
      ) : (
        <p className="text-[12.5px] text-fg-muted">
          {ns} has no IP groups.{" "}
          <Link href={nsPage(ns)} className="font-semibold text-fg-accent hover:underline">
            Add one on its page
          </Link>
          .
        </p>
      )}
    </section>
  );
}
