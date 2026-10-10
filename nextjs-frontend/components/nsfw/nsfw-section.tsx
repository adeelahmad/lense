"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { EyeOff } from "lucide-react";
import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";

import { Namespaces } from "@/app/openapi-client";
import type { NamespaceNsfw, NamespaceNsfwUpdate, NsfwChoices } from "@/app/openapi-client/types.gen";
import { isUnreachable } from "@/components/errors/error-states";
import { libraryHref } from "@/components/library/collections-model";
import { ownSwitch, parseThreshold, score, switchValue, type OwnSwitch } from "@/components/nsfw/nsfw-model";
import { ChoiceCards } from "@/components/settings/controls";
import { Button } from "@/components/ui/button";
import { Field, Input, Select } from "@/components/ui/field";
import { EmptyState, SkeletonRows } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { count, plural } from "@/lib/format";
import { needRole, useArchive } from "@/lib/hooks/session";

export const nsfwKey = (ns: string) => ["namespace", ns, "nsfw"] as const;

const DISPLAY: Record<string, string> = {
  blur: "Blur their pictures",
  hide: "Hide them from the Library, and blur",
  show: "Show them, with a label",
};

/** What undoing a change sends: the namespace's own choices as they were. */
const restore = (own: NsfwChoices | undefined): NamespaceNsfwUpdate => ({
  enabled: own?.enabled ?? null,
  threshold: own?.threshold ?? null,
  display: own?.display ?? null,
});

/**
 * A namespace's NSFW classification (docs/nsfw.md): owners turn it on or off here or follow the archive's setting, and
 * set the threshold and how flagged resources show; editors classify what was there before.
 */
export function NsfwSection({ ns, isOwner }: { ns: string; isOwner: boolean }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const { can } = useArchive();
  const editor = can("editor", ns);
  const q = useQuery({
    queryKey: nsfwKey(ns),
    queryFn: () => data(Namespaces.getNamespaceNsfw({ client, path: { name: ns } })),
  });
  const changed = (got: NamespaceNsfw) => {
    qc.setQueryData(nsfwKey(ns), got);
    void qc.invalidateQueries({ queryKey: ["recordings"] });
    void qc.invalidateQueries({ queryKey: ["recording-nsfw"] });
  };
  const save = useMutation({
    mutationFn: (body: NamespaceNsfwUpdate) =>
      data(Namespaces.updateNamespaceNsfw({ client, path: { name: ns }, body })),
    onMutate: () => ({ before: q.data?.own }),
    onSuccess: (got, body, ctx) => {
      changed(got);
      if (body.classify) return;
      toast({
        title:
          body.enabled !== undefined
            ? got.enabled
              ? `NSFW classification is on in ${ns}`
              : `NSFW classification is off in ${ns}`
            : "NSFW settings saved",
        body:
          body.enabled !== undefined && got.enabled && got.unclassified
            ? `New videos, documents and images are classified; ${plural(got.unclassified, "resource")} from before aren’t yet.`
            : undefined,
        tone: "green",
        action: { label: "Undo", onClick: () => save.mutate(restore(ctx?.before)) },
      });
    },
    onError: (e) => toast({ title: "Couldn’t save", body: (e as Error).message, tone: "red" }),
  });
  const classify = useMutation({
    mutationFn: () => data(Namespaces.classifyNamespaceNsfw({ client, path: { name: ns } })),
    onSuccess: (got) => {
      void qc.invalidateQueries({ queryKey: nsfwKey(ns) });
      toast({
        title: got.jobs.length ? `Classifying in ${plural(got.jobs.length, "run")}` : "Nothing left to classify",
        body: got.jobs.length ? "Each resource is flagged as its run gets to the nsfw step." : undefined,
        tone: got.jobs.length ? "green" : undefined,
      });
    },
    onError: (e) => toast({ title: "Couldn’t classify them", body: (e as Error).message, tone: "red" }),
  });

  const s = q.data;
  const [threshold, setThreshold] = useState("");
  useEffect(() => {
    if (s) setThreshold(s.own.threshold != null ? String(s.own.threshold) : "");
  }, [s]);
  const parsed = parseThreshold(threshold);
  const saveThreshold = (e?: FormEvent) => {
    e?.preventDefault();
    if (s && parsed !== undefined && parsed !== (s.own.threshold ?? null)) save.mutate({ threshold: parsed });
  };
  const ownerOnly = !isOwner ? needRole("owner", ns) : undefined;
  const archiveOn = s?.archive.enabled ? "on" : "off";

  return (
    <section
      id="nsfw"
      aria-labelledby="nsfw-heading"
      className="flex scroll-mt-4 flex-col gap-3.5 rounded-md border border-border bg-background px-4 py-5 sm:px-6"
    >
      <div className="flex flex-col gap-1">
        <h2 id="nsfw-heading" className="flex items-center gap-2 text-[17px] font-bold leading-tight text-fg">
          <EyeOff aria-hidden className="size-[17px] text-fg-secondary" />
          NSFW
        </h2>
        <p className="text-[13px] leading-[1.45] text-fg-secondary">
          Flag {ns}’s videos, documents and images that have NSFW pictures, with a model on this server: pictures aren’t
          sent anywhere. Blurring and hiding are display preferences, not access control.
        </p>
      </div>
      {q.isPending ? (
        <SkeletonRows rows={2} />
      ) : q.isError ? (
        <EmptyState
          tone="error"
          icon={<EyeOff />}
          title={isUnreachable(q.error) ? "Can’t reach the server" : "Couldn’t load the NSFW settings"}
          actions={<Button onClick={() => q.refetch()}>Try again</Button>}
        >
          {q.error.message}
        </EmptyState>
      ) : (
        s && (
          <>
            <ChoiceCards
              label="Classify NSFW pictures"
              size="sm"
              value={ownSwitch(s.own)}
              onChange={(v) => save.mutate({ enabled: switchValue(v as OwnSwitch) })}
              disabled={!isOwner || save.isPending}
              disabledReason={ownerOnly}
              options={[
                { value: "archive", label: "As the archive", hint: `${archiveOn} in Settings → NSFW` },
                { value: "on", label: "On", hint: "classify here" },
                { value: "off", label: "Off", hint: "never here" },
              ]}
            />
            <div className="grid gap-3 sm:grid-cols-2">
              <form onSubmit={saveThreshold}>
                <Field
                  label="Flag at score"
                  hint={
                    parsed === undefined
                      ? undefined
                      : `0.5 to 0.99; empty follows the archive’s ${score(s.archive.threshold)}`
                  }
                  error={parsed === undefined ? "A score from 0.5 to 0.99, or empty" : undefined}
                >
                  {(f) => (
                    <Input
                      id={f.id}
                      aria-describedby={f.describedBy}
                      invalid={f.invalid}
                      type="number"
                      inputMode="decimal"
                      min={0.5}
                      max={0.99}
                      step={0.01}
                      placeholder={score(s.archive.threshold)}
                      value={threshold}
                      disabled={!isOwner || save.isPending}
                      onChange={(e) => setThreshold(e.target.value)}
                      onBlur={() => saveThreshold()}
                    />
                  )}
                </Field>
              </form>
              <Field label="Flagged resources">
                {(f) => (
                  <Select
                    id={f.id}
                    aria-describedby={f.describedBy}
                    value={s.own.display ?? ""}
                    disabled={!isOwner || save.isPending}
                    onChange={(e) =>
                      save.mutate({ display: (e.target.value || null) as NamespaceNsfwUpdate["display"] })
                    }
                    options={[
                      { value: "", label: `As the archive (${DISPLAY[s.archive.display ?? "blur"].toLowerCase()})` },
                      ...Object.entries(DISPLAY).map(([value, label]) => ({ value, label })),
                    ]}
                  />
                )}
              </Field>
            </div>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-border pt-3.5">
              <p className="min-w-[220px] flex-1 text-[13px] leading-[1.45] text-fg-secondary">
                {s.enabled ? "On here" : "Off here"} · {count(s.classified ?? 0)} classified ·{" "}
                {s.flagged ? (
                  <Link className="font-semibold text-fg-accent hover:underline" href={`${libraryHref(ns)}&nsfw=only`}>
                    {count(s.flagged)} flagged
                  </Link>
                ) : (
                  "none flagged"
                )}{" "}
                · {count(s.unclassified ?? 0)} not classified yet
              </p>
              {s.enabled && (s.unclassified ?? 0) > 0 && (
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={!editor || classify.isPending}
                  disabledReason={!editor ? needRole("editor", ns) : undefined}
                  onClick={() => classify.mutate()}
                >
                  Classify existing ({count(s.unclassified ?? 0)})
                </Button>
              )}
            </div>
          </>
        )
      )}
    </section>
  );
}
