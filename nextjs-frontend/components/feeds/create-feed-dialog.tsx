"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Feeds } from "@/app/openapi-client";
import { KINDS, type Kind } from "@/components/feeds/model";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { useArchive } from "@/lib/hooks/session";

/** Make a feed: a title (focused, Enter makes it), its kind and the namespace it publishes; then open its page, where
 * its address is. The namespace is the one picked in the top bar when you can edit it, else the first you can. */
export function CreateFeedDialog({
  open,
  onOpenChange,
  kind: startKind = "rss",
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  kind?: Kind;
}) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const router = useRouter();
  const { namespaces, namespace, can } = useArchive();
  const editable = namespaces.filter((n) => can("editor", n.name));
  const [title, setTitle] = useState("");
  const [kind, setKind] = useState<Kind>(startKind);
  const [ns, setNs] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const chosen = ns || (namespace && editable.some((n) => n.name === namespace) ? namespace : editable[0]?.name) || "";

  useEffect(() => {
    if (open) {
      setError(null);
      setKind(startKind);
    }
  }, [open, startKind]);

  const make = async () => {
    if (!title.trim() || !chosen) return;
    setBusy(true);
    setError(null);
    try {
      const feed = await data(Feeds.createFeed({ client, body: { title: title.trim(), kind, namespace: chosen } }));
      void qc.invalidateQueries({ queryKey: ["feeds"] });
      onOpenChange(false);
      setTitle("");
      toast({
        title: kind === "podcast" ? "Podcast feed made" : "Feed made",
        body: "Copy its address into your app. It’s private: share it like a password.",
        tone: "green",
      });
      router.push(`/feeds/${feed.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn’t make the feed.");
    } finally {
      setBusy(false);
    }
  };

  const picked = KINDS.find((k) => k.value === kind);
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="New feed"
      description="A private address any feed reader or podcast app follows. Nothing is in it until you, a routine or an agent adds to it."
      actions={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="primary" disabled={busy || !title.trim() || !chosen} onClick={make}>
            {busy ? "Making…" : "Make feed"}
          </Button>
        </>
      }
    >
      <Field label="Title">
        {(f) => (
          <Input
            id={f.id}
            value={title}
            maxLength={200}
            placeholder={kind === "podcast" ? "e.g. This week in the archive" : "e.g. What’s new"}
            onChange={(e) => setTitle(e.target.value)}
          />
        )}
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Kind" hint={picked?.hint}>
          {(f) => (
            <Select
              id={f.id}
              aria-describedby={f.describedBy}
              value={kind}
              onChange={(e) => setKind(e.target.value as Kind)}
              options={KINDS.map((k) => ({ value: k.value, label: k.label }))}
            />
          )}
        </Field>
        <Field
          label="Namespace"
          hint={editable.length ? "Whose readers can see what the feed lists" : "You can edit no namespace"}
        >
          {(f) => (
            <Select
              id={f.id}
              aria-describedby={f.describedBy}
              value={chosen}
              disabled={!editable.length}
              onChange={(e) => setNs(e.target.value)}
              options={editable.map((n) => n.name)}
            />
          )}
        </Field>
      </div>
      {error && (
        <p role="alert" className="text-[13px] text-red-dark">
          {error}
        </p>
      )}
    </Dialog>
  );
}
