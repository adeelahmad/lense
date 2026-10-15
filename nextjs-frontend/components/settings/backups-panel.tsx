"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CloudDownload, CloudUpload, DatabaseBackup, Download } from "lucide-react";
import { useState } from "react";

import { Updates } from "@/app/openapi-client";
import type { Backup, Backups as BackupList } from "@/app/openapi-client/types.gen";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { SkeletonRows } from "@/components/ui/states";
import { Table, Td, Th, THead, Tr } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { absolute, bytes, relative } from "@/lib/format";

export const KIND_LABEL: Record<string, string> = {
  manual: "Taken by hand",
  scheduled: "Scheduled",
  "before-upgrade": "Before an upgrade",
  "before-restore": "Before a restore",
};

/** What the page says at the top: restoring, backing up, failed, just restored, or what is there. */
export function backupStatus(list: BackupList): {
  tone: "info" | "error" | "success" | "warning";
  title: string;
  body: string;
} {
  const r = list.restore;
  const s = list.schedule;
  if (r?.running)
    return {
      tone: "warning",
      title: `Restoring ${r.name}…`,
      body: "Lens is replacing the database with this backup. What it held is backed up first, and put back if the restore fails.",
    };
  if (list.in_progress?.length) return { tone: "info", title: "Backing up…", body: "Lens keeps working meanwhile." };
  if (r?.error) return { tone: "error", title: `Restoring ${r.name} failed; nothing changed.`, body: r.error };
  if (r?.finished_at && r.name)
    return {
      tone: "success",
      title: `Restored ${r.name}.`,
      body: `What was there before is kept as ${r.before ?? "a before-restore backup"}. If you’re asked to sign in again, that’s because the restored database has the sign-ins it had then.`,
    };
  if (s?.last_error) return { tone: "error", title: "The last scheduled backup failed.", body: s.last_error };
  if (!list.backups.length)
    return {
      tone: "warning",
      title: "No backups yet.",
      body:
        list.database === "memory"
          ? "The database is in memory: there is nothing to back up."
          : "Back up now, or pick a schedule below.",
    };
  return {
    tone: "info",
    title: `${list.backups.length} backup${list.backups.length === 1 ? "" : "s"}, the newest ${relative(list.backups[0].taken_at)}.`,
    body:
      s?.schedule && s.next_at ? `The next scheduled one is ${absolute(s.next_at)}.` : "None are taken on a schedule.",
  };
}

/** Settings › Backups: take one now, and the list with Download, Restore and Delete (docs/database.md#backups). */
export function BackupsPanel() {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const fail = (title: string) => (e: Error) => toast({ title, body: e.message, tone: "red" });
  const [confirm, setConfirm] = useState<{ name: string; what: "restore" | "delete" } | null>(null);
  const list = useQuery({
    queryKey: ["admin-backups"],
    queryFn: () => data(Updates.listBackups({ client })),
    refetchInterval: (q) => (q.state.data?.restore?.running || q.state.data?.in_progress?.length ? 2000 : false),
  });
  const remote = useQuery({
    queryKey: ["admin-backups-remote"],
    queryFn: () => data(Updates.remoteBackups({ client })),
    refetchInterval: (q) => (q.state.data?.copying.running ? 2000 : false),
  });
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["admin-backups"] });
    void qc.invalidateQueries({ queryKey: ["admin-backups-remote"] });
  };
  const copy = useMutation({
    mutationFn: ({ b, what }: { b: Backup; what: "send" | "fetch" }) =>
      data(
        what === "send"
          ? Updates.sendBackup({ client, path: { name: b.name } })
          : Updates.fetchBackup({ client, path: { name: b.name } }),
      ),
    onSuccess: refresh,
    onError: fail("Couldn’t copy the backup"),
  });
  const take = useMutation({
    mutationFn: () => data(Updates.takeBackup({ client })),
    onSuccess: refresh,
    onError: fail("Couldn’t start the backup"),
  });
  const download = useMutation({
    mutationFn: (b: Backup) => data(Updates.backupLink({ client, path: { name: b.name } })),
    onSuccess: (link) => {
      window.location.assign(link.url); // the browser saves it: a backup can be bigger than memory
    },
    onError: fail("Couldn’t download the backup"),
  });
  const restore = useMutation({
    mutationFn: (b: Backup) =>
      data(Updates.restoreBackup({ client, path: { name: b.name }, body: { confirm: b.name } })),
    onSuccess: () => {
      setConfirm(null);
      refresh();
    },
    onError: fail("Couldn’t restore the backup"),
  });
  const remove = useMutation({
    mutationFn: (b: Backup) => data(Updates.deleteBackup({ client, path: { name: b.name } })),
    onSuccess: (_, b) => {
      setConfirm(null);
      refresh();
      toast({ title: `Deleted ${b.name}`, tone: "green" });
    },
    onError: fail("Couldn’t delete the backup"),
  });

  if (list.isPending) return <SkeletonRows rows={3} />;
  if (list.isError) return <p className="text-[13px] text-red-dark">Couldn’t load the backups: {list.error.message}</p>;
  const l = list.data;
  const status = backupStatus(l);
  const busy = Boolean(l.restore?.running || l.in_progress?.length || take.isPending);
  const want = l.database === "embedded" ? "folder" : "export";
  const r = remote.data;
  const there = new Set((r?.backups ?? []).map((b) => b.name));
  const here = new Set(l.backups.map((b) => b.name));
  const onlyThere = (r?.backups ?? []).filter((b) => !here.has(b.name));
  const copyBusy = Boolean(r?.copying.running || copy.isPending);
  return (
    <section aria-label="Backups" className="flex flex-col gap-3">
      <Banner tone={status.tone} title={status.title}>
        {status.body}
      </Banner>
      <div>
        <Button
          variant="primary"
          size="sm"
          icon={<DatabaseBackup aria-hidden />}
          disabled={busy || l.database === "memory"}
          disabledReason={
            busy ? "A backup or restore is running" : l.database === "memory" ? "The database is in memory" : undefined
          }
          onClick={() => take.mutate()}
        >
          Back up now
        </Button>
      </div>
      {l.backups.length > 0 && (
        <div className="overflow-x-auto rounded-md border border-border">
          <Table aria-label="Backups" className="text-[13px]">
            <THead className="border-t-0">
              <tr>
                <Th>Taken</Th>
                <Th>Kind</Th>
                <Th>Size</Th>
                <Th>
                  <span className="sr-only">Actions</span>
                </Th>
              </tr>
            </THead>
            <tbody>
              {l.backups.map((b) => (
                <Tr key={b.name} className="h-11 last:border-b-0">
                  <Td className="whitespace-nowrap" title={b.name}>
                    {absolute(b.taken_at)}
                  </Td>
                  <Td className="whitespace-nowrap text-fg-secondary">{KIND_LABEL[b.label] ?? b.label}</Td>
                  <Td className="tabular whitespace-nowrap text-fg-secondary">
                    {bytes(b.bytes)}
                    {b.files ? "" : " · database only"}
                  </Td>
                  <Td className="whitespace-nowrap text-right">
                    {confirm?.name === b.name ? (
                      <span className="inline-flex items-center gap-1.5">
                        <span className="text-[12.5px] text-fg-secondary">
                          {confirm.what === "restore" ? "Replace everything with this backup?" : "Delete this backup?"}
                        </span>
                        <Button size="xs" variant="ghost" onClick={() => setConfirm(null)}>
                          Keep
                        </Button>
                        <Button
                          size="xs"
                          variant="danger"
                          disabled={restore.isPending || remove.isPending}
                          onClick={() => (confirm.what === "restore" ? restore.mutate(b) : remove.mutate(b))}
                        >
                          {confirm.what === "restore" ? "Restore" : "Delete"}
                        </Button>
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1">
                        {r?.connection &&
                          (there.has(b.name) ? (
                            <span className="px-2 text-[12px] text-fg-muted">Copied</span>
                          ) : (
                            <Button
                              size="xs"
                              variant="ghost"
                              icon={<CloudUpload aria-hidden />}
                              disabled={copyBusy}
                              disabledReason={copyBusy ? "A copy is running" : undefined}
                              aria-label={`Copy the backup of ${absolute(b.taken_at)} to the connection`}
                              onClick={() => copy.mutate({ b, what: "send" })}
                            >
                              Copy there
                            </Button>
                          ))}
                        <Button
                          size="xs"
                          variant="ghost"
                          icon={<Download aria-hidden />}
                          aria-label={`Download the backup of ${absolute(b.taken_at)}`}
                          onClick={() => download.mutate(b)}
                        >
                          Download
                        </Button>
                        <Button
                          size="xs"
                          variant="ghost"
                          disabled={busy || b.kind !== want}
                          disabledReason={
                            busy
                              ? "A backup or restore is running"
                              : b.kind !== want
                                ? "This backup is of the other kind of database"
                                : undefined
                          }
                          aria-label={`Restore the backup of ${absolute(b.taken_at)}`}
                          onClick={() => setConfirm({ name: b.name, what: "restore" })}
                        >
                          Restore…
                        </Button>
                        <Button
                          size="xs"
                          variant="danger-ghost"
                          aria-label={`Delete the backup of ${absolute(b.taken_at)}`}
                          onClick={() => setConfirm({ name: b.name, what: "delete" })}
                        >
                          Delete…
                        </Button>
                      </span>
                    )}
                  </Td>
                </Tr>
              ))}
            </tbody>
          </Table>
        </div>
      )}
      {r?.connection && (
        <RemoteNote
          error={r.error ?? null}
          lastError={r.last_error ?? null}
          copying={
            r.copying.running
              ? `${r.copying.running === "send" ? "Copying" : "Bringing back"} ${r.copying.name}…`
              : null
          }
          copyError={r.copying.error ?? null}
          count={r.backups?.length ?? 0}
        />
      )}
      {onlyThere.length > 0 && (
        <div className="overflow-x-auto rounded-md border border-border">
          <Table aria-label="Only on the connection" className="text-[13px]">
            <THead className="border-t-0">
              <tr>
                <Th>Only on the connection</Th>
                <Th>Kind</Th>
                <Th>Size</Th>
                <Th>
                  <span className="sr-only">Actions</span>
                </Th>
              </tr>
            </THead>
            <tbody>
              {onlyThere.map((b) => (
                <Tr key={b.name} className="h-11 last:border-b-0">
                  <Td className="whitespace-nowrap" title={b.name}>
                    {absolute(b.taken_at)}
                  </Td>
                  <Td className="whitespace-nowrap text-fg-secondary">{KIND_LABEL[b.label] ?? b.label}</Td>
                  <Td className="tabular whitespace-nowrap text-fg-secondary">{bytes(b.bytes)}</Td>
                  <Td className="whitespace-nowrap text-right">
                    <Button
                      size="xs"
                      variant="ghost"
                      icon={<CloudDownload aria-hidden />}
                      disabled={copyBusy}
                      disabledReason={copyBusy ? "A copy is running" : undefined}
                      aria-label={`Bring back the backup of ${absolute(b.taken_at)}`}
                      onClick={() => copy.mutate({ b, what: "fetch" })}
                    >
                      Bring back
                    </Button>
                  </Td>
                </Tr>
              ))}
            </tbody>
          </Table>
        </div>
      )}
      <p className="text-[12.5px] leading-[1.45] text-fg-secondary">
        Backups hold the database and the files Lens keeps (uploads, media, pages and the rest), and a restore puts both
        back. Keep secret.key apart from the backups: it opens the credentials and file keys they hold.
      </p>
    </section>
  );
}

/** How the copies on the connection stand: being copied, failed, or how many are there. */
export function RemoteNote({
  error,
  lastError,
  copying,
  copyError,
  count,
}: {
  error: string | null;
  lastError: string | null;
  copying: string | null;
  copyError: string | null;
  count: number;
}) {
  if (copying) return <Banner title={copying}>Lens keeps working meanwhile.</Banner>;
  if (error)
    return (
      <Banner tone="error" title="Couldn’t list the copies on the connection.">
        {error}
      </Banner>
    );
  if (copyError || lastError)
    return (
      <Banner tone="error" title="The last copy to the connection failed.">
        {copyError ?? lastError}. The backup itself is kept here; copy it again from its row.
      </Banner>
    );
  return (
    <p className="text-[12.5px] text-fg-secondary">
      {count} cop{count === 1 ? "y" : "ies"} on the connection, encrypted with this server’s secret key. A copy only
      opens with that key, so keep it somewhere other than the connection.
    </p>
  );
}
