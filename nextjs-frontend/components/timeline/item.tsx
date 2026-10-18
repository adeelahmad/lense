"use client";

import {
  Bell,
  CalendarDays,
  ChevronDown,
  ChevronRight,
  Circle,
  CircleCheck,
  Clock,
  EyeOff,
  FileText,
  Mail,
  MessagesSquare,
  NotebookPen,
  RadioTower,
  Star,
  VolumeX,
  Workflow,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { forwardRef, type KeyboardEvent, type ReactNode } from "react";

import type { Reaction } from "@/app/openapi-client/types.gen";
import { askAbout } from "@/components/page-chat/page-chat-model";
import {
  askQuote,
  familyOf,
  fieldLabel,
  groupSummary,
  objectFields,
  objectHref,
  type Activity,
  type Family,
  type Group,
  type Mark,
} from "@/components/timeline/model";
import { SocialSummary } from "@/components/timeline/social";
import { sumReactions } from "@/components/timeline/social-model";
import { Badge } from "@/components/ui/badge";
import { Button, IconButton } from "@/components/ui/button";
import { Menu, MenuContent, MenuItem, MenuTrigger } from "@/components/ui/menu";
import { DateTime } from "@/components/ui/states";
import { cn } from "@/lib/utils";

export const FAMILY_ICON: Record<Family, LucideIcon> = {
  Email: Mail,
  Calendar: CalendarDays,
  Files: FileText,
  Notes: NotebookPen,
  Sensors: RadioTower,
  Chat: MessagesSquare,
  Automations: Workflow,
  System: Bell,
};

export function KindIcon({ kind, className }: { kind: string; className?: string }) {
  const Icon = FAMILY_ICON[familyOf(kind)];
  return <Icon aria-hidden className={cn("size-4 shrink-0 text-fg-muted", className)} />;
}

/** The Reviewed and Important toggles: shown on hover or focus, and always once set. */
export function MarkToggles({
  mark,
  onMark,
  className,
}: {
  mark: Mark;
  onMark: (flags: Partial<Mark>) => void;
  className?: string;
}) {
  return (
    <span className={cn("flex shrink-0 items-center gap-0.5", className)}>
      <button
        type="button"
        aria-pressed={mark.important}
        aria-label={mark.important ? "Important (i)" : "Mark important (i)"}
        title={mark.important ? "Important (i)" : "Mark important (i)"}
        onClick={(e) => {
          e.stopPropagation();
          onMark({ important: !mark.important });
        }}
        className={cn(
          "grid size-7 place-items-center rounded-full hover:bg-surface-neutral",
          mark.important
            ? "text-gold-dark"
            : "text-fg-muted opacity-100 md:opacity-0 md:group-hover:opacity-100 md:group-focus-within:opacity-100",
        )}
      >
        <Star className={cn("size-4", mark.important && "fill-current")} />
      </button>
      <button
        type="button"
        aria-pressed={mark.reviewed}
        aria-label={mark.reviewed ? "Reviewed (r)" : "Mark reviewed (r)"}
        title={mark.reviewed ? "Reviewed (r)" : "Mark reviewed (r)"}
        onClick={(e) => {
          e.stopPropagation();
          onMark({ reviewed: !mark.reviewed });
        }}
        className={cn(
          "grid size-7 place-items-center rounded-full hover:bg-surface-neutral",
          mark.reviewed
            ? "text-green-dark"
            : "text-fg-muted opacity-100 md:opacity-0 md:group-hover:opacity-100 md:group-focus-within:opacity-100",
        )}
      >
        {mark.reviewed ? <CircleCheck className="size-4" /> : <Circle className="size-4" />}
      </button>
    </span>
  );
}

/** Ask the assistant about items, in the chat at the side of the page: its line, fields and id go with the question. */
export function AskButton({ items }: { items: Activity[] }) {
  return (
    <Button size="xs" variant="ghost" icon={<MessagesSquare />} onClick={() => askAbout(askQuote(items))}>
      Ask about this
    </Button>
  );
}

/** What an activity is about, on expand: its object's fields, what caused it, and room for what it caused. */
export function ActivityDetail({
  a,
  caused,
  social,
  onHide,
  onMute,
  kindLabel,
}: {
  a: Activity;
  /** What this activity caused (automation runs, approvals), with its own heading (timeline/caused.tsx). */
  caused?: ReactNode;
  /** Its reactions and comments (timeline/social.tsx). */
  social?: ReactNode;
  onHide?: () => void;
  /** Mute its actor (or, from the menu, its kind, for good or a day): docs/timeline.md, Muting. */
  onMute?: (opts?: MuteOpts) => void;
  /** The item's kind label, for "Mute Email received items". */
  kindLabel?: string;
}) {
  const fields = objectFields(a);
  const href = objectHref(a);
  const cause = a.lens?.cause as { automation?: number; run?: number } | null | undefined;
  return (
    <div className="flex flex-col gap-2.5 pb-1 pl-7 text-[12.5px]">
      {fields.length > 0 && (
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1">
          {fields.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-fg-muted">{fieldLabel(k)}</dt>
              <dd className="wrap-anywhere text-fg">{v}</dd>
            </div>
          ))}
        </dl>
      )}
      {cause?.automation != null && (
        <p className="text-fg-secondary">
          Caused by automation {cause.automation}
          {cause.run != null && `, run ${cause.run}`}.
        </p>
      )}
      {caused}
      {social}
      {/* the item's actions: Open, Hide, and what other modules add (Share) */}
      <div className="flex flex-wrap gap-2">
        {href && (
          <Button asChild size="xs" variant="secondary">
            <Link href={href}>Open</Link>
          </Button>
        )}
        <AskButton items={[a]} />
        {onHide && (
          <Button size="xs" variant="ghost" icon={<EyeOff />} onClick={onHide}>
            Hide
          </Button>
        )}
        {onMute && <MuteButton name={a.actor.name} kind={kindLabel} onMute={onMute} />}
      </div>
    </div>
  );
}

/** How to mute from an item: its actor (the default) or its kind, for good or for a day. */
export type MuteOpts = { kind?: boolean; day?: boolean };

/** Mute the item's actor with one click, and the other ways (for a day, its kind) behind a small menu beside it. */
function MuteButton({ name, kind, onMute }: { name: string; kind?: string; onMute: (opts?: MuteOpts) => void }) {
  return (
    <span className="inline-flex items-center">
      <Button
        size="xs"
        variant="ghost"
        icon={<VolumeX />}
        onClick={() => onMute()}
        title={`Leave everything from ${name} out of your timeline; nothing is deleted, and Undo or the Muted list brings it back`}
      >
        Mute {name}
      </Button>
      <Menu>
        <MenuTrigger asChild>
          <IconButton label={`More ways to mute ${name}`} size={24}>
            <ChevronDown />
          </IconButton>
        </MenuTrigger>
        <MenuContent align="start">
          <MenuItem icon={<Clock />} onSelect={() => onMute({ day: true })}>
            Mute {name} for a day
          </MenuItem>
          {kind && (
            <>
              <MenuItem icon={<VolumeX />} onSelect={() => onMute({ kind: true })}>
                Mute {kind} items
              </MenuItem>
              <MenuItem icon={<Clock />} onSelect={() => onMute({ kind: true, day: true })}>
                Mute {kind} items for a day
              </MenuItem>
            </>
          )}
        </MenuContent>
      </Menu>
    </span>
  );
}

type RowProps = {
  group: Group;
  /** The namespace names by id. */
  namespaces: Record<string, string>;
  /** Kind labels by name. */
  labels: Record<string, string>;
  /** The marks of a group's items: a group counts as marked when every item is. */
  markOf: (id: string) => Mark;
  onMark: (ids: string[], flags: Partial<Mark>) => void;
  onHide?: (ids: string[]) => void;
  /** Mute an item's actor, or its kind, for good or a day (docs/timeline.md, Muting). */
  onMute?: (a: Activity, opts?: MuteOpts) => void;
  open: boolean;
  onToggle: () => void;
  focused?: boolean;
  onFocus?: () => void;
  caused?: (a: Activity) => ReactNode;
  /** Everyone's reactions on an item, and how many comments it has, for the closed line's summary. */
  reactionsOf?: (id: string) => Reaction[];
  commentsOf?: (id: string) => number;
  /** An opened item's reactions and comments (timeline/social.tsx). */
  social?: (a: Activity) => ReactNode;
  /** Plain rows with no toggles (Home's card). */
  compact?: boolean;
  /** With the For you order: why an item is where it is (the page's `why`), shown under the line. */
  whyOf?: (id: string) => string | undefined;
};

/** The mark a group shows: reviewed or important once every item is. */
export function groupMark(g: Group, markOf: (id: string) => Mark): Mark {
  const ms = g.items.map((a) => markOf(a.id));
  return {
    reviewed: ms.every((m) => m.reviewed),
    important: ms.every((m) => m.important),
    hidden: ms.every((m) => m.hidden),
  };
}

/** One line of the timeline: an activity, or a run of the same actor and kind ("Work Gmail received 37 emails"),
 * opened with a click to its items or its details. */
export const TimelineRow = forwardRef<HTMLLIElement, RowProps>(function TimelineRow(
  {
    group,
    namespaces,
    labels,
    markOf,
    onMark,
    onHide,
    onMute,
    open,
    onToggle,
    focused,
    onFocus,
    caused,
    reactionsOf,
    commentsOf,
    social,
    compact,
    whyOf,
  },
  ref,
) {
  const first = group.items[0];
  const last = group.items[group.items.length - 1];
  const many = group.items.length > 1;
  const ids = group.items.map((a) => a.id);
  const mark = groupMark(group, markOf);
  const ns = first.space != null ? namespaces[String(first.space)] : undefined;
  const href = !many ? objectHref(first) : null;
  const summary = groupSummary(group, labels[first.kind]);
  const reactions = reactionsOf ? sumReactions(group.items.map((a) => reactionsOf(a.id))) : [];
  const comments = commentsOf ? group.items.reduce((n, a) => n + commentsOf(a.id), 0) : 0;
  const why = whyOf?.(first.id);
  const onKey = (e: KeyboardEvent) => {
    if (e.target !== e.currentTarget) return;
    if (e.key === "Enter" || e.key === "o") {
      e.preventDefault();
      onToggle();
    }
  };
  return (
    <li
      ref={ref}
      tabIndex={compact ? undefined : 0}
      data-active={focused || undefined}
      data-testid="timeline-row"
      onFocus={onFocus}
      onKeyDown={compact ? undefined : onKey}
      className={cn(
        "group flex flex-col gap-1.5 border-b border-border px-1 py-2.5 outline-hidden last:border-b-0",
        !compact && "focus-visible:bg-blue-surface/40",
        focused && !compact && "bg-surface-neutral/60",
        mark.reviewed && !compact && "opacity-75",
      )}
    >
      <div className="flex items-start gap-2">
        {!compact && (
          <button
            type="button"
            tabIndex={-1}
            aria-expanded={open}
            aria-label={open ? "Close" : many ? `Show the ${group.items.length}` : "Show details"}
            onClick={onToggle}
            className="mt-0.5 grid size-5 shrink-0 place-items-center rounded-sm text-fg-secondary hover:bg-surface-neutral"
          >
            {open ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
          </button>
        )}
        <KindIcon kind={first.kind} className="mt-0.5" />
        <span className="flex min-w-0 flex-1 flex-col gap-[3px]">
          <span className="flex min-w-0 items-center gap-2">
            {href ? (
              <Link
                href={href}
                className={cn(
                  "truncate text-[13.5px] font-semibold leading-tight text-fg hover:underline",
                  !mark.reviewed && "font-bold",
                )}
              >
                {summary}
              </Link>
            ) : (
              <button
                type="button"
                tabIndex={-1}
                onClick={onToggle}
                className="truncate text-left text-[13.5px] font-semibold leading-tight text-fg hover:underline"
              >
                {summary}
              </button>
            )}
            {many && (
              <Badge mono className="shrink-0">
                {group.items.length}
              </Badge>
            )}
          </span>
          <span className="truncate text-[12px] leading-tight text-fg-muted">
            {[first.actor.name, ns, labels[first.kind]].filter(Boolean).join(" · ")} ·{" "}
            <DateTime iso={first.published} />
            {many && (
              <>
                {" – "}
                <DateTime iso={last.published} />
              </>
            )}
          </span>
          {why && (
            <span data-testid="why" className="truncate text-[12px] leading-tight text-blue-dark">
              {why}
            </span>
          )}
        </span>
        {!compact && <MarkToggles mark={mark} onMark={(flags) => onMark(ids, flags)} />}
      </div>
      {!open && !compact && <SocialSummary reactions={reactions} comments={comments} />}
      {open &&
        !compact &&
        (many ? (
          <ul className="ml-7 flex flex-col border-l border-border pl-3">
            {group.items.map((a) => (
              <SubItem
                key={a.id}
                a={a}
                mark={markOf(a.id)}
                onMark={(flags) => onMark([a.id], flags)}
                onHide={onHide ? () => onHide([a.id]) : undefined}
                onMute={onMute ? (opts) => onMute(a, opts) : undefined}
                kindLabel={labels[a.kind]}
                caused={caused?.(a)}
                social={social?.(a)}
              />
            ))}
          </ul>
        ) : (
          <ActivityDetail
            a={first}
            caused={caused?.(first)}
            social={social?.(first)}
            onHide={onHide ? () => onHide(ids) : undefined}
            onMute={onMute ? (opts) => onMute(first, opts) : undefined}
            kindLabel={labels[first.kind]}
          />
        ))}
    </li>
  );
});

function SubItem({
  a,
  mark,
  onMark,
  onHide,
  onMute,
  kindLabel,
  caused,
  social,
}: {
  a: Activity;
  mark: Mark;
  onMark: (flags: Partial<Mark>) => void;
  onHide?: () => void;
  onMute?: (opts?: MuteOpts) => void;
  kindLabel?: string;
  caused?: ReactNode;
  social?: ReactNode;
}) {
  const href = objectHref(a);
  return (
    <li className="group flex flex-col gap-1 py-1.5">
      <div className="flex items-center gap-2">
        <span className="flex min-w-0 flex-1 flex-col">
          {href ? (
            <Link href={href} className="truncate text-[13px] font-medium text-fg hover:underline">
              {a.summary}
            </Link>
          ) : (
            <span className="truncate text-[13px] font-medium text-fg">{a.summary}</span>
          )}
          <span className="text-[11.5px] text-fg-muted">
            <DateTime iso={a.published} />
          </span>
        </span>
        <MarkToggles mark={mark} onMark={onMark} />
      </div>
      {caused}
      {social}
      <div className="flex flex-wrap gap-2">
        <AskButton items={[a]} />
        {onHide && (
          <Button size="xs" variant="ghost" icon={<EyeOff />} onClick={onHide}>
            Hide
          </Button>
        )}
        {onMute && <MuteButton name={a.actor.name} kind={kindLabel} onMute={onMute} />}
      </div>
    </li>
  );
}
