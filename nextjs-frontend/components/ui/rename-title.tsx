"use client";

import { Pencil } from "lucide-react";
import { useState } from "react";

import { Button, IconButton } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { cn } from "@/lib/utils";

/** A title that can be renamed in place: the pencil opens a field with the name selected, Enter saves and Escape
 * keeps the old name. `onRename` saves it (and refreshes what shows the name); without it the title is plain. A page
 * title by default; `as="span"` for a name in a list or table. */
export function RenameTitle({
  name,
  what,
  onRename,
  as: Tag = "h1",
  className,
  inputClassName,
}: {
  name: string;
  /** What is being renamed, for the labels ("pipeline" → "Rename pipeline"). */
  what: string;
  onRename?: (name: string) => Promise<unknown>;
  as?: "h1" | "span";
  className?: string;
  inputClassName?: string;
}) {
  const toast = useToast();
  const [draft, setDraft] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const save = async () => {
    const next = (draft ?? "").trim();
    if (!next || next === name || !onRename) return setDraft(null);
    setSaving(true);
    try {
      await onRename(next);
      setDraft(null);
    } catch (e) {
      toast({ tone: "red", title: "Couldn’t rename it", body: e instanceof Error ? e.message : String(e) });
    } finally {
      setSaving(false);
    }
  };
  if (draft != null)
    return (
      <form
        className="flex items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <Input
          aria-label={`${what[0].toUpperCase()}${what.slice(1)} name`}
          value={draft}
          maxLength={80}
          autoFocus
          onFocus={(e) => e.currentTarget.select()}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === "Escape" && setDraft(null)}
          className={cn("h-9 w-[280px] text-[16px] font-bold", inputClassName)}
        />
        <Button type="submit" size="sm" disabled={!draft.trim() || saving}>
          {saving ? "Saving…" : "Save"}
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={() => setDraft(null)}>
          Cancel
        </Button>
      </form>
    );
  return (
    <span className="flex min-w-0 items-center gap-1">
      <Tag className={cn("truncate text-[18px] font-bold text-fg", className)}>{name}</Tag>
      {onRename && (
        <IconButton label={`Rename ${what}`} size={30} onClick={() => setDraft(name)}>
          <Pencil />
        </IconButton>
      )}
    </span>
  );
}
