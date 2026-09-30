"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Copy, Pencil, ScanText } from "lucide-react";
import { useMemo, useState } from "react";

import { Video } from "@/app/openapi-client";
import { usePlayerApi, usePlayerState } from "@/components/player/media";
import { useRec } from "@/components/recording/context";
import { rk } from "@/components/recording/hooks";
import type { ScreenText } from "@/components/recording/model";
import { groupByShot, shotAt } from "@/components/recording/video/model";
import { Button } from "@/components/ui/button";
import { SearchInput } from "@/components/ui/field";
import { EmptyState } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { Tooltip } from "@/components/ui/tooltip";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { tc } from "@/lib/format";
import { needRole } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

/**
 * Text on screen (VR1): lines grouped by shot, with the frame and each line's box. Click a line to seek; editors
 * correct a line in place (E, or the pencil). Corrections are searchable and logged; the machine reading is kept.
 */
export function ScreenTextTab({ noEngine }: { noEngine?: boolean }) {
  const { model, id, canEdit, ns } = useRec();
  const api = usePlayerApi();
  const { time } = usePlayerState();
  const toast = useToast();
  const [q, setQ] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const groups = useMemo(() => groupByShot(model.screenText, model.shots, q), [model.screenText, model.shots, q]);
  const curShot = shotAt(model.shots, time);

  if (!model.screenText.length)
    return noEngine ? (
      <div
        className="rounded-md border border-gold-border bg-gold-surface p-3.5 text-[13px] leading-normal text-fg-strong"
        role="status"
      >
        <b className="text-fg">Text on screen isn&apos;t set up.</b> No OCR engine is configured, so slides and captions
        aren&apos;t read. Admins choose Tesseract, Apple Vision (on a Mac worker) or RapidOCR in Settings.
      </div>
    ) : (
      <EmptyState icon={<ScanText />} title="No text on screen" className="py-10">
        The Text on screen step reads slides and captions from sampled frames. None was found in this video, or the step
        hasn&apos;t run.
      </EmptyState>
    );

  const copyShot = async () => {
    const g = groups.find((x) => x.index === curShot) ?? groups[0];
    if (!g) return;
    try {
      await navigator.clipboard.writeText(g.lines.map((l) => l.text).join("\n"));
      toast({
        title: `Copied ${g.lines.length} ${g.lines.length === 1 ? "line" : "lines"} from shot ${g.index + 1}`,
        tone: "green",
      });
    } catch {
      toast({ title: "Couldn't copy", tone: "red" });
    }
  };

  return (
    <>
      <div className="flex items-center gap-2">
        <SearchInput
          className="flex-1"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder={`Search ${model.screenText.length} lines on screen`}
          aria-label="Search the text on screen"
        />
        <Button variant="ghost" size="sm" icon={<Copy />} onClick={() => void copyShot()}>
          Copy shot
        </Button>
      </div>
      {!groups.length && <p className="py-4 text-center text-[13px] text-fg-muted">No line on screen matches “{q}”.</p>}
      {groups.map((g) => {
        const on = g.index === curShot;
        return (
          <section
            key={g.index}
            aria-label={g.shot ? `Shot ${g.index + 1}` : "Before the first shot"}
            className={cn(
              "grid grid-cols-[minmax(100px,150px)_minmax(0,1fr)] gap-3.5 rounded-md border p-2.5",
              on ? "border-blue bg-blue-surface" : "border-border bg-background",
            )}
          >
            <div className="flex flex-col gap-[5px]">
              <button
                type="button"
                onClick={() => api.seek(g.shot?.t0 ?? g.lines[0].t0, { manual: true })}
                className="relative aspect-video overflow-hidden rounded-[6px] bg-black"
                aria-label={`Play shot ${g.index + 1}`}
              >
                {(g.lines[0].frame ?? g.shot?.frame) && (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={(g.lines[0].frame ?? g.shot?.frame) as string}
                    alt=""
                    className="size-full object-cover"
                    loading="lazy"
                  />
                )}
                {g.lines
                  .filter((l) => l.box)
                  .slice(0, 6)
                  .map((l) => (
                    <span
                      key={l.id}
                      aria-hidden
                      className="absolute rounded-[2px] border-2 border-[var(--aladdin-gold)]"
                      style={{
                        left: `${l.box![0] * 100}%`,
                        top: `${l.box![1] * 100}%`,
                        width: `${l.box![2] * 100}%`,
                        height: `${l.box![3] * 100}%`,
                      }}
                    />
                  ))}
              </button>
              <span className="tabular text-[11.5px] font-semibold leading-tight text-fg-secondary">
                {g.shot ? `Shot ${g.index + 1} · ${tc(g.shot.t0)}–${tc(g.shot.t1)}` : "Opening"}
              </span>
            </div>
            <ul className="m-0 flex min-w-0 list-none flex-col gap-1 p-0">
              {g.lines.map((l) =>
                editing === l.id ? (
                  <li key={l.id}>
                    <LineEditor recId={id} line={l} onDone={() => setEditing(null)} />
                  </li>
                ) : (
                  <li
                    key={l.id}
                    className={cn(
                      "group flex items-center gap-2 rounded-[6px] border border-transparent px-2 py-1",
                      time >= l.t0 && time < l.t1 && "bg-hl",
                    )}
                  >
                    <button
                      type="button"
                      onClick={() => api.seek(l.t0, { manual: true })}
                      onKeyDown={(e) => {
                        if ((e.key === "e" || e.key === "E") && canEdit) {
                          e.preventDefault();
                          setEditing(l.id);
                        }
                      }}
                      aria-keyshortcuts={canEdit ? "E" : undefined}
                      className={cn(
                        "min-w-0 flex-1 text-left text-[13.5px] leading-[1.35] text-fg",
                        time >= l.t0 && time < l.t1 && "font-semibold",
                      )}
                      title={`${tc(l.t0)}–${tc(l.t1)}${canEdit ? " · press E to correct" : ""}`}
                    >
                      {l.text}
                    </button>
                    {l.edited && <span className="text-[11px] font-medium text-gold-dark">fixed</span>}
                    {canEdit ? (
                      <Tooltip content="Correct this line (E)">
                        <button
                          type="button"
                          aria-label={`Correct “${l.text}”`}
                          onClick={() => setEditing(l.id)}
                          className="grid size-6 shrink-0 place-items-center rounded-full text-fg-muted opacity-0 hover:bg-surface-neutral hover:text-fg focus-visible:opacity-100 group-hover:opacity-100"
                        >
                          <Pencil className="size-3" />
                        </button>
                      </Tooltip>
                    ) : (
                      <Tooltip content={`Only editors can correct text. ${needRole("editor", ns)}.`}>
                        <span
                          aria-hidden
                          className="grid size-6 shrink-0 place-items-center text-fg-muted opacity-0 group-hover:opacity-40"
                        >
                          <Pencil className="size-3" />
                        </span>
                      </Tooltip>
                    )}
                  </li>
                ),
              )}
            </ul>
          </section>
        );
      })}
    </>
  );
}

function LineEditor({ recId, line, onDone }: { recId: number; line: ScreenText; onDone: () => void }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const [text, setText] = useState(line.text);
  const save = useMutation({
    mutationFn: () =>
      data(
        Video.fixScreenText({
          client,
          path: { rid: recId, span: line.id },
          body: { text: text.trim() },
        }),
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: rk.player(recId) });
      toast({
        title: "Line corrected",
        body: "Search uses the correction; the machine reading is kept.",
        tone: "green",
      });
      onDone();
    },
    onError: (e) =>
      toast({
        title: "Couldn't save the correction",
        body: e instanceof ApiError ? e.message : undefined,
        tone: "red",
      }),
  });
  return (
    <form
      className="flex items-center gap-1.5 rounded-[6px] border border-blue px-1.5 py-1"
      onSubmit={(e) => {
        e.preventDefault();
        if (text.trim() && text.trim() !== line.text) save.mutate();
        else onDone();
      }}
    >
      <input
        autoFocus
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => e.key === "Escape" && onDone()}
        aria-label="Corrected text"
        className="h-7 min-w-0 flex-1 bg-transparent px-1 text-[13.5px] text-fg outline-none"
      />
      <Button type="submit" size="xs" variant="primary" disabled={save.isPending || !text.trim()}>
        Save
      </Button>
      <Button size="xs" variant="ghost" onClick={onDone}>
        Cancel
      </Button>
    </form>
  );
}
