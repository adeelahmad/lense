"use client";

import { Highlighter, Link2, MessageSquare, MessagesSquare, Share2, StickyNote } from "lucide-react";
import dynamic from "next/dynamic";
import { useEffect, useState, type RefObject } from "react";

import { newHighlightBody } from "@/components/recording/comments-model";
import { useRec } from "@/components/recording/context";
import { useHighlightActions } from "@/components/recording/hooks";
import { draftFromSelection } from "@/components/recording/notes-model";
import { Dialog } from "@/components/ui/dialog";
import { useToast } from "@/components/ui/toast";
import { tc } from "@/lib/format";
import { needRole } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

// The IIIF content-state dialog brings the IIIF panels with it: loaded when a moment is shared, not with the text.
const ShareMoment = dynamic(() => import("@/components/iiif/iiif-panel").then((m) => m.ShareMoment), { ssr: false });

/** The time of a character inside a line, spread evenly over the line's duration (there are no word timings). */
export function timeAtOffset(seg: { t0: number; t1: number; text: string }, offset: number): number {
  const f = seg.text.length ? Math.max(0, Math.min(1, offset / seg.text.length)) : 0;
  return Math.round(seg.t0 + (seg.t1 - seg.t0) * f);
}

/** Selecting text offers Copy link at that moment, a IIIF link (recordings), Ask in chat (with the quote), Add note
 * and Comment (about that moment, quoting it) and, for editors, Highlight (in yellow; the Highlights tab recolours
 * and labels it). The text's lines carry `data-seg` with their segment's index in `model.segments`. */
export function SelectionToolbar({ box }: { box: RefObject<HTMLDivElement | null> }) {
  const { id, ns, model, canEdit, paged, where, askInChat, addNote, addComment } = useRec();
  const { create: highlight } = useHighlightActions(id);
  const toast = useToast();
  const [sel, setSel] = useState<{
    x: number;
    y: number;
    t: number;
    end: number;
    quote: string;
  } | null>(null);
  const [moment, setMoment] = useState<{ t0: number; t1: number } | null>(null);
  useEffect(() => {
    const onChange = () => {
      const s = window.getSelection();
      const container = box.current;
      if (!s || s.isCollapsed || !s.rangeCount || !container) return setSel(null);
      const range = s.getRangeAt(0);
      if (!container.contains(range.commonAncestorContainer)) return setSel(null);
      const quote = s.toString().trim();
      if (quote.length < 2) return setSel(null);
      const startEl = (
        range.startContainer.nodeType === 1 ? range.startContainer : range.startContainer.parentElement
      ) as HTMLElement | null;
      const segEl = startEl?.closest<HTMLElement>("[data-seg]");
      const seg = segEl ? model.segments[Number(segEl.dataset.seg)] : null;
      let offset = 0;
      if (segEl && seg) {
        const pre = document.createRange();
        pre.selectNodeContents(segEl);
        pre.setEnd(range.startContainer, range.startOffset);
        offset = pre.toString().length;
      }
      const endEl = (
        range.endContainer.nodeType === 1 ? range.endContainer : range.endContainer.parentElement
      ) as HTMLElement | null;
      const endSegEl = endEl?.closest<HTMLElement>("[data-seg]");
      const endSeg = endSegEl ? model.segments[Number(endSegEl.dataset.seg)] : null;
      let endOffset = 0;
      if (endSegEl && endSeg) {
        const pre = document.createRange();
        pre.selectNodeContents(endSegEl);
        pre.setEnd(range.endContainer, range.endOffset);
        endOffset = pre.toString().length;
      }
      const rect = range.getBoundingClientRect();
      const t = seg ? timeAtOffset(seg, offset) : 0;
      setSel({
        x: rect.left + rect.width / 2,
        y: rect.top,
        t,
        end: endSeg ? Math.max(t, timeAtOffset(endSeg, endOffset)) : t,
        quote,
      });
    };
    document.addEventListener("selectionchange", onChange);
    const hide = () => setSel(null);
    const el = box.current;
    el?.addEventListener("scroll", hide);
    return () => {
      document.removeEventListener("selectionchange", onChange);
      el?.removeEventListener("scroll", hide);
    };
  }, [box, model.segments]);
  const momentDialog = (
    <Dialog
      open={moment != null}
      onOpenChange={(o) => !o && setMoment(null)}
      title="Share a moment"
      description="A IIIF link that opens this time range in Lens and in any viewer that supports content state."
    >
      {moment && <ShareMoment recordingId={id} initial={moment} />}
    </Dialog>
  );
  if (!sel) return momentDialog;
  const width = typeof window !== "undefined" ? window.innerWidth : 1200;
  const narrow = width < 640;
  const secs = Math.floor(sel.t / 1000);
  const copy = async () => {
    const url = `${window.location.origin}/resources/${id}?t=${secs}`;
    try {
      await navigator.clipboard.writeText(url);
      toast({ title: `Link at ${paged ? where(sel.t) : tc(sel.t)} copied`, tone: "green" });
    } catch {
      toast({ title: "Couldn't copy the link", body: url, tone: "red" });
    }
  };
  return (
    <>
      {momentDialog}
      <div
        role="toolbar"
        aria-label="Selection actions"
        onMouseDown={(e) => e.preventDefault()}
        className={cn(
          "fixed z-[60] flex -translate-y-[calc(100%+10px)] gap-0.5 whitespace-nowrap rounded-[10px] bg-fg p-1 text-[12.5px] font-semibold text-background shadow-3",
          // on a phone it spans the screen and wraps its actions; elsewhere it follows the words
          narrow ? "inset-x-2 flex-wrap justify-center" : "-translate-x-1/2",
        )}
        style={{
          left: narrow ? undefined : Math.max(300, Math.min(sel.x, width - 300)),
          top: Math.max(narrow ? 110 : 70, sel.y),
        }}
      >
        <button
          type="button"
          onClick={() => void copy()}
          className="flex h-[30px] items-center gap-1.5 rounded-[7px] px-2.5 hover:bg-white/15"
        >
          <Link2 className="size-3.5" /> Copy link at {paged ? where(sel.t) : tc(sel.t)}
        </button>
        {!paged && (
          <button
            type="button"
            onClick={() => {
              setMoment({
                t0: Math.floor(sel.t / 1000),
                t1: Math.max(Math.floor(sel.t / 1000) + 1, Math.ceil(sel.end / 1000)),
              });
              window.getSelection()?.removeAllRanges();
              setSel(null);
            }}
            className="flex h-[30px] items-center gap-1.5 rounded-[7px] px-2.5 hover:bg-white/15"
          >
            <Share2 className="size-3.5" /> IIIF link
          </button>
        )}
        <button
          type="button"
          onClick={() => {
            askInChat(`> “${sel.quote.length > 400 ? `${sel.quote.slice(0, 397)}…` : sel.quote}” (${tc(sel.t)})\n\n`);
            window.getSelection()?.removeAllRanges();
            setSel(null);
          }}
          className="flex h-[30px] items-center gap-1.5 rounded-[7px] px-2.5 hover:bg-white/15"
        >
          <MessagesSquare className="size-3.5" /> Ask in chat
        </button>
        <button
          type="button"
          onClick={() => {
            addNote(draftFromSelection(sel.t, sel.end, sel.quote));
            window.getSelection()?.removeAllRanges();
            setSel(null);
          }}
          className="flex h-[30px] items-center gap-1.5 rounded-[7px] px-2.5 hover:bg-white/15"
        >
          <StickyNote className="size-3.5" /> Add note
        </button>
        <button
          type="button"
          onClick={() => {
            addComment(draftFromSelection(sel.t, sel.end, sel.quote));
            window.getSelection()?.removeAllRanges();
            setSel(null);
          }}
          className="flex h-[30px] items-center gap-1.5 rounded-[7px] px-2.5 hover:bg-white/15"
        >
          <MessageSquare className="size-3.5" /> Comment
        </button>
        <button
          type="button"
          aria-disabled={!canEdit || undefined}
          title={
            canEdit ? "Highlight it in yellow; the Highlights tab recolours and labels it" : needRole("editor", ns)
          }
          onClick={() => {
            if (!canEdit) return;
            highlight.mutate(newHighlightBody(draftFromSelection(sel.t, sel.end, sel.quote)));
            window.getSelection()?.removeAllRanges();
            setSel(null);
          }}
          className={cn(
            "flex h-[30px] items-center gap-1.5 rounded-[7px] px-2.5",
            canEdit ? "hover:bg-white/15" : "cursor-not-allowed opacity-50",
          )}
        >
          <Highlighter className="size-3.5" /> Highlight
        </button>
      </div>
    </>
  );
}
