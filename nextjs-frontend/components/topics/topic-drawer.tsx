"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Merge, Trash2, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { Topics } from "@/app/openapi-client";
import { broaderChoices, splitLabels, type TopicItem } from "@/components/topics/model";
import { TopicPicker } from "@/components/topics/topic-picker";
import { Badge } from "@/components/ui/badge";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Dialog, Drawer } from "@/components/ui/dialog";
import { Field, Input, Textarea } from "@/components/ui/field";
import { Skeleton } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { count } from "@/lib/format";
import { needRole, useArchive } from "@/lib/hooks/session";

const SOURCE: Record<string, string> = { person: "added", entity: "from the entity", analysis: "from analysis" };

const same = (a: number[], b: number[]) => a.length === b.length && a.every((x, i) => x === b[i]);

/** One topic: its labels, definition and place in the vocabulary, and the recordings about it. Editors change it here. */
export function TopicDrawer({
  id,
  ns,
  all,
  onSelect,
  onClose,
}: {
  id: number;
  ns: string;
  all: TopicItem[];
  onSelect: (id: number) => void;
  onClose: () => void;
}) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const { can } = useArchive();
  const canEdit = can("editor", ns);
  const t = useQuery({
    queryKey: ["topic", id],
    queryFn: () => data(Topics.getTopic({ client, path: { tid: id } })),
  });

  const [label, setLabel] = useState("");
  const [alt, setAlt] = useState("");
  const [definition, setDefinition] = useState("");
  const [broader, setBroader] = useState<number[]>([]);
  const [related, setRelated] = useState<number[]>([]);
  const [merging, setMerging] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const d = t.data;
  useEffect(() => {
    if (d) {
      setLabel(d.label);
      setAlt((d.alt ?? []).join(", "));
      setDefinition(d.definition ?? "");
      setBroader((d.broader ?? []).map((x) => x.id));
      setRelated((d.related ?? []).map((x) => x.id));
    }
  }, [d]);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["topic"] });
    qc.invalidateQueries({ queryKey: ["topics"] });
    qc.invalidateQueries({ queryKey: ["recording-topics"] });
  };
  const fail = (title: string) => (err: unknown) =>
    toast({ title, body: err instanceof ApiError ? err.message : "Please try again.", tone: "red" });

  const altList = splitLabels(alt);
  const dirty =
    Boolean(d) &&
    (label.trim() !== d!.label ||
      altList.join("\n") !== (d!.alt ?? []).join("\n") ||
      definition.trim() !== (d!.definition ?? "") ||
      !same(
        broader,
        (d!.broader ?? []).map((x) => x.id),
      ) ||
      !same(
        related,
        (d!.related ?? []).map((x) => x.id),
      ));
  const save = useMutation({
    mutationFn: () =>
      data(
        Topics.updateTopic({
          client,
          path: { tid: id },
          body: { label: label.trim(), alt: altList, definition: definition.trim(), broader, related },
        }),
      ),
    onSuccess: () => {
      refresh();
      toast({ title: "Saved", tone: "green" });
    },
    onError: fail("Couldn’t save the topic"),
  });
  const tag = useMutation({
    mutationFn: ({ recording, remove }: { recording: number; remove: boolean }) =>
      data(Topics.tagRecordings({ client, path: { tid: id }, body: { recordings: [recording], remove } })),
    onSuccess: refresh,
    onError: fail("Couldn’t change it"),
  });
  const remove = useMutation({
    mutationFn: () => data(Topics.deleteTopic({ client, path: { tid: id } })),
    onSuccess: () => {
      refresh();
      qc.invalidateQueries({ queryKey: ["entities"] });
      toast({ title: "Topic deleted", tone: "green" });
      onClose();
    },
    onError: fail("Couldn’t delete it"),
  });

  const accepted = (d?.about ?? []).filter((a) => a.status === "accepted");
  const suggested = (d?.about ?? []).filter((a) => a.status === "suggested");
  return (
    <Drawer
      open
      onOpenChange={(o) => !o && onClose()}
      title={d?.label ?? "Topic"}
      width={460}
      actions={
        d &&
        canEdit && (
          <>
            <Button size="xs" variant="ghost" icon={<Merge />} onClick={() => setMerging(true)}>
              Merge
            </Button>
            <Button size="xs" variant="danger-ghost" icon={<Trash2 />} onClick={() => setDeleting(true)}>
              Delete
            </Button>
          </>
        )
      }
    >
      <div className="flex flex-col gap-5 p-4">
        {t.isLoading && <Skeleton className="h-40 w-full" />}
        {t.isError && <Banner tone="error">{t.error.message}</Banner>}
        {d && (
          <>
            {d.from_entity ? (
              <p className="m-0 text-[12.5px] text-fg-secondary">
                Made from an entity of the same name, hidden while this topic exists. Deleting the topic shows it again.
              </p>
            ) : null}
            <form
              className="flex flex-col gap-3"
              onSubmit={(ev) => {
                ev.preventDefault();
                save.mutate();
              }}
            >
              <Field label="Label">
                {(ids) => (
                  <Input
                    id={ids.id}
                    value={label}
                    required
                    maxLength={200}
                    disabled={!canEdit}
                    onChange={(ev) => setLabel(ev.target.value)}
                  />
                )}
              </Field>
              <Field label="Other labels" optional hint="Synonyms, abbreviations and spellings, separated by commas.">
                {(ids) => (
                  <Input
                    id={ids.id}
                    aria-describedby={ids.describedBy}
                    value={alt}
                    disabled={!canEdit}
                    onChange={(ev) => setAlt(ev.target.value)}
                  />
                )}
              </Field>
              <Field label="Definition" optional hint="What the topic covers. The assistant reads it too.">
                {(ids) => (
                  <Textarea
                    id={ids.id}
                    aria-describedby={ids.describedBy}
                    rows={3}
                    maxLength={2000}
                    value={definition}
                    disabled={!canEdit}
                    placeholder={canEdit ? "e.g. Treating disease by changing genes" : "No definition"}
                    onChange={(ev) => setDefinition(ev.target.value)}
                  />
                )}
              </Field>
              <Field label="Broader topics" optional hint="The wider topics this one belongs under.">
                {(ids) => (
                  <TopicPicker
                    id={ids.id}
                    describedBy={ids.describedBy}
                    value={broader}
                    onChange={setBroader}
                    choices={broaderChoices(all, id)}
                    all={all}
                    disabled={!canEdit}
                  />
                )}
              </Field>
              <Field label="Related topics" optional>
                {(ids) => (
                  <TopicPicker
                    id={ids.id}
                    value={related}
                    onChange={setRelated}
                    choices={all.filter((x) => x.id !== id)}
                    all={all}
                    disabled={!canEdit}
                  />
                )}
              </Field>
              {canEdit ? (
                <div>
                  <Button
                    type="submit"
                    size="sm"
                    variant="primary"
                    disabled={!dirty || !label.trim() || save.isPending}
                  >
                    Save
                  </Button>
                </div>
              ) : (
                <p className="m-0 text-[12.5px] text-fg-muted">{needRole("editor", ns)}</p>
              )}
            </form>
            {(d.narrower ?? []).length > 0 && (
              <section className="flex flex-col gap-2">
                <h3 className="m-0 text-[13px] font-bold text-fg-strong">Narrower topics</h3>
                <ul className="m-0 flex list-none flex-wrap gap-1.5 p-0">
                  {d.narrower!.map((n) => (
                    <li key={n.id}>
                      <button
                        type="button"
                        onClick={() => onSelect(n.id)}
                        className="h-7 rounded-pill border border-border px-2.5 text-[12.5px] font-semibold text-fg hover:border-fg-accent hover:text-fg-accent"
                      >
                        {n.label}
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            )}
            <section className="flex flex-col gap-2">
              <h3 className="m-0 text-[13px] font-bold text-fg-strong">
                Recordings about it <span className="font-normal text-fg-muted">· {count(accepted.length)}</span>
              </h3>
              {!accepted.length && !suggested.length && (
                <p className="m-0 text-[13px] text-fg-secondary">
                  None yet. Add this topic to a recording from its Entities tab.
                </p>
              )}
              <ul className="m-0 flex list-none flex-col gap-1 p-0">
                {[...suggested, ...accepted].map((a) => (
                  <li key={a.recording} className="flex min-h-8 items-center gap-2 text-[13px]">
                    <Link
                      href={`/recordings/${a.recording}`}
                      className="min-w-0 flex-1 truncate font-semibold text-fg hover:text-fg-accent hover:underline"
                    >
                      {a.title ?? `Recording ${a.recording}`}
                    </Link>
                    {a.status === "suggested" ? (
                      <Badge tone="intent">suggested</Badge>
                    ) : (
                      <span className="shrink-0 text-[12px] text-fg-muted">{SOURCE[a.source ?? ""] ?? a.source}</span>
                    )}
                    {canEdit && a.status === "suggested" && (
                      <button
                        type="button"
                        aria-label={`Accept for ${a.title ?? `recording ${a.recording}`}`}
                        disabled={tag.isPending}
                        onClick={() => tag.mutate({ recording: a.recording, remove: false })}
                        className="grid size-7 place-items-center rounded-full text-fg-muted hover:bg-surface-neutral hover:text-fg"
                      >
                        <Check className="size-3.5" />
                      </button>
                    )}
                    {canEdit && (
                      <button
                        type="button"
                        aria-label={`Not about this: ${a.title ?? `recording ${a.recording}`}`}
                        disabled={tag.isPending}
                        onClick={() => tag.mutate({ recording: a.recording, remove: true })}
                        className="grid size-7 place-items-center rounded-full text-fg-muted hover:bg-surface-neutral hover:text-fg"
                      >
                        <X className="size-3.5" />
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          </>
        )}
      </div>
      {d && (
        <MergeDialog
          open={merging}
          onOpenChange={setMerging}
          keep={d.id}
          label={d.label}
          all={all}
          onMerged={() => {
            refresh();
            setMerging(false);
          }}
        />
      )}
      <Dialog
        open={deleting}
        onOpenChange={setDeleting}
        title={`Delete ${d?.label ?? "this topic"}?`}
        description="Its narrower topics move up to its broader ones, and recordings stop being about it."
        actions={
          <>
            <Button variant="ghost" onClick={() => setDeleting(false)}>
              Cancel
            </Button>
            <Button variant="danger" disabled={remove.isPending} onClick={() => remove.mutate()}>
              Delete topic
            </Button>
          </>
        }
      />
    </Drawer>
  );
}

/** Fold other topics into this one: their labels become its other labels, their recordings are about it. */
function MergeDialog({
  open,
  onOpenChange,
  keep,
  label,
  all,
  onMerged,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  keep: number;
  label: string;
  all: TopicItem[];
  onMerged: () => void;
}) {
  const client = useApiClient();
  const [others, setOthers] = useState<number[]>([]);
  const merge = useMutation({
    mutationFn: () => data(Topics.mergeTopics({ client, body: { keep, others } })),
    onSuccess: () => {
      setOthers([]);
      onMerged();
    },
  });
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={`Merge into ${label}`}
      description="The topics you pick are deleted; their labels become other labels of this one, and their recordings are about it."
      actions={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="primary" disabled={!others.length || merge.isPending} onClick={() => merge.mutate()}>
            Merge
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-2">
        <TopicPicker
          value={others}
          onChange={setOthers}
          choices={all.filter((t) => t.id !== keep)}
          all={all}
          placeholder="Pick a topic to merge…"
        />
        {merge.isError && (
          <p role="alert" className="m-0 text-[13px] text-red-dark">
            {merge.error instanceof ApiError ? merge.error.message : "Couldn’t merge them. Please try again."}
          </p>
        )}
      </div>
    </Dialog>
  );
}

/** Add a topic to a namespace's vocabulary, optionally under broader topics. */
export function NewTopicDialog({
  ns,
  all,
  open,
  under,
  onOpenChange,
  onCreated,
}: {
  ns: string;
  all: TopicItem[];
  open: boolean;
  under: number | null;
  onOpenChange: (o: boolean) => void;
  onCreated: (id: number) => void;
}) {
  const client = useApiClient();
  const [label, setLabel] = useState("");
  const [alt, setAlt] = useState("");
  const [definition, setDefinition] = useState("");
  const [broader, setBroader] = useState<number[]>([]);
  useEffect(() => {
    if (open) setBroader(under ? [under] : []);
  }, [open, under]);
  const create = useMutation({
    mutationFn: () =>
      data(
        Topics.createTopic({
          client,
          path: { name: ns },
          body: { label: label.trim(), alt: splitLabels(alt), definition: definition.trim() || null, broader },
        }),
      ),
    onSuccess: (t) => {
      setLabel("");
      setAlt("");
      setDefinition("");
      onCreated(t.id);
    },
  });
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Add topic"
      description={`A subject the recordings of ${ns} can be about.`}
      actions={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="primary" form="new-topic" type="submit" disabled={!label.trim() || create.isPending}>
            Add topic
          </Button>
        </>
      }
    >
      <form
        id="new-topic"
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
      >
        <Field label="Label">
          {(ids) => (
            <Input id={ids.id} value={label} required maxLength={200} onChange={(e) => setLabel(e.target.value)} />
          )}
        </Field>
        <Field label="Other labels" optional hint="Synonyms, abbreviations and spellings, separated by commas.">
          {(ids) => (
            <Input
              id={ids.id}
              aria-describedby={ids.describedBy}
              value={alt}
              onChange={(e) => setAlt(e.target.value)}
            />
          )}
        </Field>
        <Field label="Definition" optional>
          {(ids) => (
            <Textarea
              id={ids.id}
              rows={3}
              maxLength={2000}
              value={definition}
              onChange={(e) => setDefinition(e.target.value)}
            />
          )}
        </Field>
        <Field label="Broader topics" optional>
          {(ids) => (
            <TopicPicker
              id={ids.id}
              value={broader}
              onChange={setBroader}
              choices={broaderChoices(all, null)}
              all={all}
            />
          )}
        </Field>
        {create.isError && (
          <p role="alert" className="m-0 text-[13px] text-red-dark">
            {create.error instanceof ApiError ? create.error.message : "Couldn’t add it. Please try again."}
          </p>
        )}
      </form>
    </Dialog>
  );
}
