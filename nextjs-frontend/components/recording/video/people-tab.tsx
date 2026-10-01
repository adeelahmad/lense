"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Ellipsis, ScanFace, UserRoundX } from "lucide-react";
import Link from "next/link";
import { useState, type FormEvent } from "react";

import { Video } from "@/app/openapi-client";
import { usePlayerApi } from "@/components/player/media";
import { useRec } from "@/components/recording/context";
import { rk, useNamespaceFaces } from "@/components/recording/hooks";
import { facePages, pageRef } from "@/components/recording/document/model";
import type { FaceTrack } from "@/components/recording/model";
import { screenTime } from "@/components/recording/video/model";
import { useFaceColors } from "@/components/recording/video/face-colors";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/field";
import { Menu, MenuContent, MenuItem, MenuLabel, MenuSeparator, MenuTrigger } from "@/components/ui/menu";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { tc } from "@/lib/format";
import { needRole } from "@/lib/hooks/session";

type NsFace = {
  id: number;
  display?: string;
  speaker?: number | null;
  speaker_name?: string | null;
  suggestions?: { kind: string; id: number; name: string; score: number }[];
};

/**
 * People on screen (VR1/VR2, VP1): per the namespace's face setting — off (nothing detected or stored), detect only
 * (boxes and screen time) or recognise (names matched across the namespace, suggestions to confirm, link to a voice).
 * On a document's or an image's pages (`onPage`), faces say which pages they're on and turn to them.
 */
export function PeopleTab({ noFaces, onPage }: { noFaces?: boolean; onPage?: (page: number) => void }) {
  const { model, ns, canEdit, canEditNamespace } = useRec();
  // faces are the namespace's: only its members see its face registry and change names, links and merges
  const nsFaces = useNamespaceFaces(ns, model.facesMode === "recognize");
  const color = useFaceColors();
  const byId = new Map(((nsFaces.data?.faces ?? []) as NsFace[]).map((f) => [f.id, f]));

  if (model.facesMode === "off")
    return (
      <Notice title={`Face recognition is off in ${ns ?? "this namespace"}`}>
        No faces are detected or stored for this namespace. An owner can turn it on, or choose “detect only”, after
        recording the purpose. Nothing here changes until they do.
      </Notice>
    );
  if (!model.faces.length)
    return onPage ? (
      <EmptyState icon={<ScanFace />} title="No faces on its pages" className="py-10">
        The Faces step looks for people on the pages once they’re drawn; it found none, or hasn’t run yet.
      </EmptyState>
    ) : noFaces ? (
      <Notice title="No faces in this video">
        The Faces step ran on the sampled frames and found none above the detection threshold — typical for slide-only
        recordings.
      </Notice>
    ) : (
      <EmptyState icon={<ScanFace />} title="No people on screen yet" className="py-10">
        The Faces step finds people on screen after the shots are sampled.
      </EmptyState>
    );

  return (
    <>
      {model.facesMode === "detect" && (
        <Notice title="Faces are counted, not identified">
          This namespace is set to detect only: {onPage ? "boxes and pages" : "boxes and screen time"}, no names, no
          matching, no face registry.
        </Notice>
      )}
      <ul className="m-0 flex list-none flex-col p-0" aria-label={onPage ? "People on its pages" : "People on screen"}>
        {model.faces.map((f, i) => (
          <PersonRow
            key={f.id}
            track={f}
            index={i}
            color={color(i)}
            face={f.face ? byId.get(f.face) : undefined}
            loading={nsFaces.isLoading && model.facesMode === "recognize"}
            canEdit={canEdit}
            canEditNamespace={canEditNamespace}
            ns={ns}
            onPage={onPage}
          />
        ))}
      </ul>
    </>
  );
}

function Notice({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div role="status" className="rounded-md border border-border bg-surface p-3.5">
      <div className="text-[13.5px] font-bold leading-snug text-fg">{title}</div>
      <p className="mt-1 text-[13px] leading-normal text-fg-secondary">{children}</p>
    </div>
  );
}

function useFaceMutations() {
  const { id, ns } = useRec();
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const done = (title: string) => () => {
    void qc.invalidateQueries({ queryKey: rk.player(id) });
    void qc.invalidateQueries({ queryKey: ["faces", ns] });
    toast({ title, tone: "green" });
  };
  const fail = (title: string) => (e: unknown) =>
    toast({
      title,
      body: e instanceof ApiError ? e.message : undefined,
      tone: "red",
    });
  return {
    rename: useMutation({
      mutationFn: (v: { fid: number; name: string }) =>
        data(
          Video.renameFace({
            client,
            path: { fid: v.fid },
            body: { name: v.name },
          }),
        ),
      onSuccess: done("Renamed"),
      onError: fail("Couldn't rename"),
    }),
    merge: useMutation({
      mutationFn: (v: { fid: number; into: number }) =>
        data(
          Video.mergeFace({
            client,
            path: { fid: v.fid },
            body: { into: v.into },
          }),
        ),
      onSuccess: done("Merged: the same person"),
      onError: fail("Couldn't merge"),
    }),
    link: useMutation({
      mutationFn: (v: { fid: number; speaker: number | null }) =>
        data(
          Video.linkFaceSpeaker({
            client,
            path: { fid: v.fid },
            body: { speaker: v.speaker },
          }),
        ),
      onSuccess: done("Face and voice updated"),
      onError: fail("Couldn't link the voice"),
    }),
    dismiss: useMutation({
      mutationFn: (v: { fid: number; kind: string; other: number }) =>
        data(
          Video.dismissFaceSuggestion({
            client,
            path: { fid: v.fid },
            body: { kind: v.kind, id: v.other },
          }),
        ),
      onSuccess: done("Suggestion dismissed"),
      onError: fail("Couldn't dismiss it"),
    }),
    notAFace: useMutation({
      mutationFn: (track: string) => data(Video.deleteFaceTrack({ client, path: { rid: id, track } })),
      onSuccess: done("Removed: not a face"),
      onError: fail("Couldn't remove it"),
    }),
  };
}

function PersonRow({
  track,
  index,
  color,
  face,
  loading,
  canEdit: canEditRecording,
  canEditNamespace: canEdit,
  ns,
  onPage,
}: {
  track: FaceTrack;
  index: number;
  color: string;
  face?: NsFace;
  loading: boolean;
  /** May change this recording (remove a face track from it). */
  canEdit: boolean;
  /** May change the namespace's faces (names, links to voices, merges). */
  canEditNamespace: boolean;
  ns: string | null;
  /** On a document's pages: turn to one (spans and boxes count pages, from 0). */
  onPage?: (page: number) => void;
}) {
  const { model } = useRec();
  const api = usePlayerApi();
  const m = useFaceMutations();
  const [renaming, setRenaming] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [name, setName] = useState(track.name);
  const recognize = model.facesMode === "recognize";
  const label = recognize ? track.name : `Face ${index + 1}`;
  const fid = track.face;
  const faceSugg = (face?.suggestions ?? []).filter((s) => s.kind === "face").sort((a, b) => b.score - a.score)[0];
  const voiceSugg = (face?.suggestions ?? []).filter((s) => s.kind === "speaker").sort((a, b) => b.score - a.score)[0];
  const reason = needRole("editor", ns);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (fid) m.rename.mutate({ fid, name: name.trim() }, { onSuccess: () => setRenaming(false) });
  };
  return (
    <li className="flex flex-col gap-2 border-b border-border py-2.5 last:border-b-0">
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={() => (onPage ? onPage(track.firstMs) : api.seek(track.firstMs, { manual: true }))}
          aria-label={
            onPage
              ? `Turn to ${label}'s first page, ${pageRef(model.pages, track.firstMs)}`
              : `Go to ${label}'s first appearance, ${tc(track.firstMs)}`
          }
          className="grid size-11 shrink-0 place-items-center overflow-hidden rounded-[10px] border-2 bg-surface-neutral text-[10px] text-fg-muted"
          style={{ borderColor: color }}
        >
          {track.cover ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={track.cover} alt="" className="size-full object-cover" loading="lazy" />
          ) : (
            "face"
          )}
        </button>
        {renaming ? (
          <form onSubmit={submit} className="flex min-w-0 flex-1 items-center gap-2">
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoFocus
              maxLength={80}
              aria-label={`Name for ${label}`}
              className="h-8 text-[13.5px]"
            />
            <Button type="submit" size="xs" variant="primary" disabled={m.rename.isPending}>
              Save
            </Button>
            <Button size="xs" variant="ghost" onClick={() => setRenaming(false)}>
              Cancel
            </Button>
          </form>
        ) : (
          <div className="min-w-0 flex-1">
            <div className="truncate text-[14px] font-bold leading-tight text-fg">{label}</div>
            <div className="text-[12.5px] leading-snug text-fg-secondary">
              {onPage
                ? `On ${facePages(model.pages, track.spans)}`
                : `${screenTime(track.screenMs)} on screen · first at ${tc(track.firstMs)}`}
              {face?.speaker_name && (
                <>
                  {" "}
                  · linked to voice{" "}
                  <Link href={`/speakers/${face.speaker}`} className="font-semibold hover:underline">
                    {face.speaker_name}
                  </Link>
                </>
              )}
            </div>
            {loading && <Skeleton className="mt-1 h-2.5 w-32" />}
          </div>
        )}
        {!renaming && (
          <Menu>
            <MenuTrigger asChild>
              <button
                type="button"
                aria-label={`Actions for ${label}`}
                className="grid size-8 shrink-0 place-items-center rounded-full text-fg-secondary hover:bg-surface-neutral"
              >
                <Ellipsis className="size-4" />
              </button>
            </MenuTrigger>
            <MenuContent className="min-w-[240px]">
              {!canEdit && <MenuLabel>{reason}</MenuLabel>}
              {recognize && fid && (
                <>
                  <MenuItem disabled={!canEdit} onSelect={() => setRenaming(true)}>
                    Rename (everywhere in {ns})
                  </MenuItem>
                  <MenuLabel>Link to a voice</MenuLabel>
                  {model.speakers.map((s) => (
                    <MenuItem
                      key={s.id}
                      disabled={!canEdit || face?.speaker === s.id}
                      onSelect={() => m.link.mutate({ fid, speaker: s.id })}
                      shortcut={face?.speaker === s.id ? "linked" : undefined}
                    >
                      {s.name}
                    </MenuItem>
                  ))}
                  {face?.speaker != null && (
                    <MenuItem disabled={!canEdit} onSelect={() => m.link.mutate({ fid, speaker: null })}>
                      Unlink the voice
                    </MenuItem>
                  )}
                  <MenuSeparator />
                </>
              )}
              <MenuItem danger icon={<UserRoundX />} disabled={!canEditRecording} onSelect={() => setConfirm(true)}>
                Not a face
              </MenuItem>
            </MenuContent>
          </Menu>
        )}
      </div>
      {recognize && fid && faceSugg && (
        <Suggestion
          text={
            <>
              maybe <b>{faceSugg.name}</b> · {faceSugg.score.toFixed(2)} · unsure
            </>
          }
          yes="Same person"
          onYes={() => m.merge.mutate({ fid, into: faceSugg.id })}
          no="Not the same"
          onNo={() => m.dismiss.mutate({ fid, kind: "face", other: faceSugg.id })}
          disabled={!canEdit ? reason : undefined}
        />
      )}
      {recognize && fid && voiceSugg && face?.speaker == null && (
        <Suggestion
          text={
            <>
              on screen while <b>{voiceSugg.name}</b> speaks · {Math.round(voiceSugg.score * 100)}% of the time
            </>
          }
          yes={`Link to ${voiceSugg.name}`}
          onYes={() => m.link.mutate({ fid, speaker: voiceSugg.id })}
          no="Dismiss"
          onNo={() => m.dismiss.mutate({ fid, kind: "speaker", other: voiceSugg.id })}
          disabled={!canEdit ? reason : undefined}
        />
      )}
      <Dialog
        open={confirm}
        onOpenChange={setConfirm}
        title="Not a face?"
        description={`Removes ${label}'s boxes and crop from this ${onPage ? "resource" : "recording"}. It can't be undone; running the Faces step again may find it again.`}
        actions={
          <>
            <Button variant="ghost" onClick={() => setConfirm(false)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              onClick={() =>
                m.notAFace.mutate(track.id, {
                  onSuccess: () => setConfirm(false),
                })
              }
              disabled={m.notAFace.isPending}
            >
              Remove
            </Button>
          </>
        }
      />
    </li>
  );
}

function Suggestion({
  text,
  yes,
  onYes,
  no,
  onNo,
  disabled,
}: {
  text: React.ReactNode;
  yes: string;
  onYes: () => void;
  no: string;
  onNo: () => void;
  disabled?: string;
}) {
  return (
    <div className="ml-14 flex flex-wrap items-center gap-2 rounded-sm border border-gold-border bg-gold-surface px-2.5 py-1.5 text-[12.5px] text-gold-dark">
      <span aria-hidden className="size-[7px] rotate-45 rounded-[1px] bg-gold" />
      <span className="min-w-0 flex-1 text-fg-strong">{text}</span>
      <Button size="xs" variant="approve" disabled={Boolean(disabled)} disabledReason={disabled} onClick={onYes}>
        {yes}
      </Button>
      <Button size="xs" variant="ghost" disabled={Boolean(disabled)} disabledReason={disabled} onClick={onNo}>
        {no}
      </Button>
    </div>
  );
}
