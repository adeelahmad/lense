"use client";

import { createContext, useContext, type ReactNode } from "react";

import type { RecordingDetail } from "@/components/recording/hooks";
import type { JobInfo, PageState } from "@/components/recording/jobs";
import type { EntityRef, FindHit, PlayerModel, SpeakerInfo, Turn } from "@/components/recording/model";
import type { NoteDraft } from "@/components/recording/notes-model";
import type { Role } from "@/lib/hooks/session";

export type PanelTab =
  | "summary"
  | "speakers"
  | "entities"
  | "chat"
  | "notes"
  | "history"
  | "metadata"
  | "iiif"
  | "details"
  | "files"
  | "shots"
  | "text"
  | "people"
  | "transcript";

/** Everything the recording page's parts share: the data, the person's role, and page-level UI state. */
export type RecordingCtx = {
  id: number;
  rec: RecordingDetail;
  model: PlayerModel;
  turns: Turn[];
  speakers: Map<string, SpeakerInfo>;
  state: PageState;
  jobs: JobInfo[];
  ns: string | null;
  /** Their role on the recording: through its namespace, or its collection (an admin of it is an owner). */
  role: Role | undefined;
  canEdit: boolean;
  /** An editor of the namespace: speakers, faces and entities are the namespace's, so only they change those. */
  canEditNamespace: boolean;
  /** A role in the namespace (not only on the recording's collection): chat and the namespace's lists need one. */
  member: boolean;
  /** No playable media: an imported transcript. */
  transcriptOnly: boolean;
  find: {
    open: boolean;
    query: string;
    hits: FindHit[];
    index: number;
    setOpen: (o: boolean) => void;
    setQuery: (q: string) => void;
    setIndex: (i: number) => void;
  };
  entity: { selected: EntityRef | null; select: (e: EntityRef | null) => void };
  tab: PanelTab;
  setTab: (t: PanelTab) => void;
  /** A file to show in the Files tab (and one of its lines), from the page's address (?file=&line=). */
  fileFocus?: { file: number; line: number | null } | null;
  /** A quote waiting to prefill the recording chat ("Ask in chat"). */
  chatDraft: string | null;
  askInChat: (quote: string) => void;
  clearChatDraft: () => void;
  /** A moment and its words waiting to start a note (the transcript's "Add note"). */
  noteDraft: NoteDraft | null;
  addNote: (draft: NoteDraft) => void;
  clearNoteDraft: () => void;
  editing: boolean;
  setEditing: (on: boolean) => void;
  openReprocess: () => void;
  openShare: (startMs?: number) => void;
  openRename: () => void;
  openAccess: () => void;
  /** Attach audio to a transcript-only recording (editors). */
  openAttach: () => void;
  /** Move it into another collection of its namespace (editors). */
  openCollection: () => void;
};

const Ctx = createContext<RecordingCtx | null>(null);

export function RecordingProvider({ value, children }: { value: RecordingCtx; children: ReactNode }) {
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useRec(): RecordingCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useRec needs a RecordingProvider");
  return c;
}
