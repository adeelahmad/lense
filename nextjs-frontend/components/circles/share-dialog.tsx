"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useState } from "react";

import { Circles } from "@/app/openapi-client";
import { firstPick, reach, whom } from "@/components/circles/model";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Checkbox, Field, Switch, Textarea } from "@/components/ui/field";
import { Skeleton } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";

/** Share one activity or resource with the person's circles and a note (docs/circles.md, Sharing). Each person gets it
 * in their own timeline; the toast's Undo takes it back. */
export function ShareDialog({
  open,
  onOpenChange,
  activity,
  recording,
  what,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  activity?: string;
  recording?: number;
  /** What's shared, for the description ("“Trip on Friday”"). */
  what: string;
}) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const circles = useQuery({
    queryKey: ["circles"],
    queryFn: () => data(Circles.listCircles({ client })),
    enabled: open,
  });
  const list = circles.data?.circles ?? [];
  const [picked, setPicked] = useState<number[]>([]);
  const [note, setNote] = useState("");
  const [withItem, setWithItem] = useState(false);

  useEffect(() => {
    if (open) setPicked(firstPick(circles.data?.circles ?? []));
  }, [open, circles.data]);

  const undo = useMutation({
    mutationFn: (sid: number) => data(Circles.undoCircleShare({ client, path: { sid } })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["timeline"] });
      toast({ title: "Share taken back" });
    },
  });
  const share = useMutation({
    mutationFn: () =>
      data(
        Circles.shareWithCircles({
          client,
          body: {
            activity: activity ?? null,
            recording: recording ?? null,
            circles: picked,
            note: note.trim() || null,
            with_item: recording != null && withItem,
          },
        }),
      ),
    onSuccess: (out) => {
      void qc.invalidateQueries({ queryKey: ["timeline"] });
      onOpenChange(false);
      setNote("");
      setWithItem(false);
      toast({
        title: `Shared with ${whom(list, picked)}`,
        body: out.granted ? `${out.granted} of them can now open it.` : undefined,
        tone: "green",
        action: { label: "Undo", onClick: () => undo.mutate(out.id) },
      });
    },
  });

  const toggle = (id: number, on: boolean) => setPicked((p) => (on ? [...p, id] : p.filter((x) => x !== id)));
  const people = reach(list, picked);
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Share with your circles"
      description={`Everyone you pick sees ${what} and your note in their own timeline.`}
      actions={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={!picked.length || share.isPending}
            disabledReason={!picked.length ? "Pick a circle first" : undefined}
            onClick={() => share.mutate()}
          >
            {share.isPending ? "Sharing…" : people ? `Share with ${people}` : "Share"}
          </Button>
        </>
      }
    >
      {circles.isPending ? (
        <Skeleton className="h-16 w-full" />
      ) : list.length === 0 ? (
        <p className="text-[13.5px] text-fg-secondary">
          You have no circles yet.{" "}
          <Link href="/account#circles" className="font-semibold text-blue hover:underline">
            Make one under Profile
          </Link>
          , with the people you want to share with.
        </p>
      ) : (
        <fieldset className="flex flex-col gap-2">
          <legend className="mb-1 text-[13px] font-bold text-fg-strong">Circles</legend>
          {list.map((c) => (
            <Checkbox
              key={c.id}
              checked={picked.includes(c.id)}
              onCheckedChange={(on) => toggle(c.id, on)}
              label={
                <>
                  {c.name} <span className="text-fg-muted">· {c.members.length}</span>
                </>
              }
            />
          ))}
        </fieldset>
      )}
      <Field label="Note" optional hint="What you want them to know.">
        {(f) => (
          <Textarea
            id={f.id}
            aria-describedby={f.describedBy}
            rows={2}
            maxLength={2000}
            value={note}
            placeholder="e.g. Sam's trip is on Friday: he needs a packed lunch"
            onChange={(e) => setNote(e.target.value)}
          />
        )}
      </Field>
      {recording != null && (
        <Switch
          checked={withItem}
          onCheckedChange={setWithItem}
          label="With the item: let them open it too (needs publish)"
        />
      )}
      {share.isError && (
        <p role="alert" className="text-[13px] text-red-dark">
          {share.error.message}
        </p>
      )}
    </Dialog>
  );
}
