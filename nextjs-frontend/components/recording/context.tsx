"use client";

import { createContext, useContext, type ReactNode } from "react";

import type { RecordingDetail } from "@/components/recording/hooks";
import type { JobInfo, PageState } from "@/components/recording/jobs";
import type { EntityRef, FindHit, PlayerModel, SpeakerInfo, Turn } from "@/components/recording/model";
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
  role: Role | undefined;
  canEdit: boolean;
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
  /** A quote waiting to prefill the recording chat ("Ask in chat"). */
  chatDraft: string | null;
  askInChat: (quote: string) => void;
  clearChatDraft: () => void;
  editing: boolean;
  setEditing: (on: boolean) => void;
  openReprocess: () => void;
  openShare: (startMs?: number) => void;
  openRename: () => void;
  openAccess: () => void;
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
