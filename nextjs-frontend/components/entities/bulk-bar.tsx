"use client";

import { ChevronDown, GitMerge, Shapes, X } from "lucide-react";

import type { EntityType } from "@/app/openapi-client/types.gen";
import { keepOf } from "@/components/entities/curate";
import type { EntityRow } from "@/components/entities/model";
import { Menu, MenuContent, MenuItem, MenuLabel, MenuTrigger } from "@/components/ui/menu";
import { count, plural } from "@/lib/format";
import { cn } from "@/lib/utils";

const actionCls =
  "flex h-[34px] items-center gap-1.5 whitespace-nowrap rounded-pill px-3 text-[13px] font-semibold outline-offset-0 [&_svg]:size-[15px] hover:bg-[color-mix(in_srgb,var(--background)_16%,transparent)] disabled:cursor-not-allowed disabled:opacity-50";

/** The Entities page's floating bar for the rows picked: merge them into one (the one to keep is guessed and can be
 * changed) or give them all one type. Both happen at once and can be undone from the toast. */
export function EntityBulkBar({
  picked,
  types,
  busy,
  onMerge,
  onRetype,
  onClear,
}: {
  picked: EntityRow[];
  types: EntityType[];
  busy: boolean;
  onMerge: (keep: EntityRow) => void;
  onRetype: (type: string) => void;
  onClear: () => void;
}) {
  if (!picked.length) return null;
  const keep = keepOf(picked)!;
  const many = picked.length > 1;
  return (
    <div className="pointer-events-none sticky bottom-5 z-30 mt-2 flex justify-center px-4">
      <div
        role="toolbar"
        aria-label={`${plural(picked.length, "entity", "entities")} selected`}
        className="pointer-events-auto flex max-w-full items-center gap-1 overflow-x-auto rounded-pill bg-fg py-1.5 pl-4 pr-1.5 text-background shadow-3 scrollbar-none"
      >
        <span className="whitespace-nowrap pr-2.5 text-[13px] font-semibold" aria-live="polite">
          {count(picked.length)} selected
        </span>
        <button
          type="button"
          className={cn(actionCls, "pr-2")}
          disabled={!many || busy}
          title={many ? undefined : "Pick two or more to merge them"}
          onClick={() => onMerge(keep)}
        >
          <GitMerge aria-hidden />
          {many ? `Merge into ${keep.name.length > 24 ? `${keep.name.slice(0, 23)}…` : keep.name}` : "Merge"}
        </button>
        <Menu>
          <MenuTrigger className={cn(actionCls, "-ml-1 px-1.5")} disabled={!many || busy} aria-label="Keep another one">
            <ChevronDown aria-hidden />
          </MenuTrigger>
          <MenuContent align="center" className="max-h-[50dvh] overflow-y-auto">
            <MenuLabel>Merge them all into</MenuLabel>
            {picked.map((e) => (
              <MenuItem key={e.id} onSelect={() => onMerge(e)}>
                <span className="min-w-0 flex-1 truncate">{e.name}</span>
                <span className="text-[12px] text-fg-muted">{plural(e.mentions, "mention")}</span>
              </MenuItem>
            ))}
          </MenuContent>
        </Menu>
        <Menu>
          <MenuTrigger className={actionCls} disabled={busy}>
            <Shapes aria-hidden />
            Type
          </MenuTrigger>
          <MenuContent align="center" className="max-h-[50dvh] overflow-y-auto">
            <MenuLabel>Make them all</MenuLabel>
            {types.map((t) => (
              <MenuItem key={t.type} onSelect={() => onRetype(t.type)}>
                {t.label}
              </MenuItem>
            ))}
          </MenuContent>
        </Menu>
        <button
          type="button"
          onClick={onClear}
          aria-label="Clear selection"
          className="grid size-[34px] shrink-0 place-items-center rounded-full hover:bg-[color-mix(in_srgb,var(--background)_16%,transparent)]"
        >
          <X className="size-4" />
        </button>
      </div>
    </div>
  );
}
