"use client";

import { Ellipsis } from "lucide-react";
import { useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";

import { ChatTab } from "@/components/recording/chat-tab";
import { useRec, type PanelTab } from "@/components/recording/context";
import { DetailsTab } from "@/components/recording/details-tab";
import { EntitiesTab } from "@/components/recording/entities-tab";
import { HistoryTab } from "@/components/recording/history-tab";
import { IiifTab, MetadataTab } from "@/components/recording/iiif-tab";
import { SpeakersTab } from "@/components/recording/speakers-tab";
import { SummaryTab } from "@/components/recording/summary-tab";
import { fitTabs } from "@/components/recording/tab-fit";
import { Menu, MenuContent, MenuItem, MenuSeparator, MenuTrigger } from "@/components/ui/menu";
import { Tooltip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

export type TabDef = {
  value: PanelTab;
  label: string;
  count?: number | string;
  disabled?: string;
};

export const AUDIO_TABS: TabDef[] = [
  { value: "summary", label: "Summary" },
  { value: "speakers", label: "Speakers" },
  { value: "entities", label: "Entities" },
  { value: "chat", label: "Chat" },
  { value: "notes", label: "Notes", disabled: "Notes aren't available yet" },
  { value: "history", label: "History" },
];
export const MORE_TABS: TabDef[] = [
  { value: "metadata", label: "Metadata" },
  { value: "iiif", label: "IIIF" },
  { value: "details", label: "Details" },
];

/**
 * Underlined tabs with an overflow menu (…). Tabs that don't fit the panel's width move into the menu with the `more`
 * tabs (priority+), and the active tab always stays on the row. Arrow keys move between the tabs on the row.
 */
export function PanelTabs({
  tabs,
  more = [],
  value,
  onChange,
  idBase,
  className,
}: {
  tabs: TabDef[];
  more?: TabDef[];
  value: PanelTab;
  onChange: (t: PanelTab) => void;
  idBase: string;
  className?: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const measure = useRef<HTMLDivElement>(null);
  const [dims, setDims] = useState<{
    avail: number;
    moreW: number;
    widths: Record<string, number>;
  } | null>(null);
  const all = [...tabs, ...more];
  const sig = all.map((t) => `${t.value}:${t.label}:${t.count ?? ""}`).join("|");

  useLayoutEffect(() => {
    const el = box.current;
    const m = measure.current;
    if (!el || !m) return;
    const calc = () => {
      const widths: Record<string, number> = {};
      m.querySelectorAll<HTMLElement>("[data-v]").forEach((n) => {
        widths[n.dataset.v as string] = n.offsetWidth;
      });
      const moreW = m.querySelector<HTMLElement>("[data-more]")?.offsetWidth ?? 34;
      const cs = getComputedStyle(el);
      const avail = el.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
      setDims((d) =>
        d && d.avail === avail && d.moreW === moreW && Object.keys(widths).every((k) => d.widths[k] === widths[k])
          ? d
          : { avail, moreW, widths },
      );
    };
    calc();
    const ro = new ResizeObserver(calc);
    ro.observe(el);
    ro.observe(m);
    return () => ro.disconnect();
  }, [sig]);

  const byValue = new Map(all.map((t) => [t.value as string, t]));
  const fit = dims
    ? fitTabs(
        tabs.map((t) => t.value),
        more.map((t) => t.value),
        (v) => dims.widths[v] ?? 0,
        dims.avail,
        dims.moreW,
        value,
      )
    : {
        shown: [...tabs.map((t) => t.value as string), ...(more.some((t) => t.value === value) ? [value] : [])],
        overflow: more.filter((t) => t.value !== value).map((t) => t.value as string),
      };
  const shown = fit.shown.map((v) => byValue.get(v)).filter((t): t is TabDef => Boolean(t));
  const overflow = fit.overflow.map((v) => byValue.get(v)).filter((t): t is TabDef => Boolean(t));
  const overflowTabs = overflow.filter((t) => tabs.includes(t));
  const overflowMore = overflow.filter((t) => !tabs.includes(t));

  const onKey = (e: KeyboardEvent) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
    const els = Array.from(
      list.current?.querySelectorAll<HTMLButtonElement>("[role=tab]:not([aria-disabled=true])") ?? [],
    );
    const i = els.indexOf(document.activeElement as HTMLButtonElement);
    const next =
      e.key === "Home"
        ? 0
        : e.key === "End"
          ? els.length - 1
          : (i + (e.key === "ArrowRight" ? 1 : -1) + els.length) % els.length;
    els[next]?.focus();
    els[next]?.click();
    e.preventDefault();
  };
  const tabCls = (on: boolean, disabled?: boolean) =>
    cn(
      "-mb-px inline-flex h-[42px] shrink-0 items-center gap-1.5 whitespace-nowrap border-b-[3px] px-[11px] text-[14px] transition-colors duration-fast",
      on ? "border-blue font-bold text-blue" : "border-transparent font-medium text-fg-secondary hover:text-fg",
      disabled && "cursor-not-allowed opacity-50 hover:text-fg-secondary",
    );
  const count = (t: TabDef) =>
    t.count != null && <span className="font-mono text-[11px] font-medium text-fg-muted">{t.count}</span>;
  const item = (t: TabDef) => (
    <MenuItem
      key={t.value}
      onSelect={() => onChange(t.value)}
      disabled={Boolean(t.disabled)}
      shortcut={t.disabled ? "Not available yet" : t.count != null ? String(t.count) : undefined}
    >
      {t.label}
    </MenuItem>
  );
  return (
    <div ref={box} className={cn("relative flex shrink-0 items-end border-b border-border px-1.5", className)}>
      {/* Every tab at its widest (bold), off screen, to measure what fits. */}
      <div ref={measure} aria-hidden className="pointer-events-none invisible absolute left-0 top-0 flex w-max">
        {all.map((t) => (
          <span key={t.value} data-v={t.value} className={tabCls(true)}>
            {t.label}
            {count(t)}
          </span>
        ))}
        <span data-more className={cn(tabCls(false), "px-2")}>
          <Ellipsis className="size-[18px]" />
        </span>
      </div>
      <div
        ref={list}
        role="tablist"
        aria-label="Recording panels"
        onKeyDown={onKey}
        className="flex min-w-0 flex-1 overflow-x-auto [scrollbar-width:none]"
      >
        {shown.map((t) => {
          const on = t.value === value;
          const btn = (
            <button
              key={t.value}
              type="button"
              role="tab"
              id={`${idBase}-tab-${t.value}`}
              aria-selected={on}
              aria-controls={`${idBase}-panel`}
              aria-disabled={t.disabled ? true : undefined}
              tabIndex={on ? 0 : -1}
              onClick={() => !t.disabled && onChange(t.value)}
              className={tabCls(on, Boolean(t.disabled))}
            >
              {t.label}
              {count(t)}
            </button>
          );
          return t.disabled ? (
            <Tooltip key={t.value} content={t.disabled}>
              {btn}
            </Tooltip>
          ) : (
            btn
          );
        })}
      </div>
      {overflow.length > 0 && (
        <Menu>
          <MenuTrigger asChild>
            <button
              type="button"
              aria-label={`More panels: ${overflow.map((t) => t.label).join(", ")}`}
              title={`More: ${overflow.map((t) => t.label).join(", ")}`}
              className={cn(tabCls(false), "px-2")}
            >
              <Ellipsis className="size-[18px]" />
            </button>
          </MenuTrigger>
          <MenuContent className="min-w-[200px]">
            {overflowTabs.map(item)}
            {overflowTabs.length > 0 && overflowMore.length > 0 && <MenuSeparator />}
            {overflowMore.map(item)}
          </MenuContent>
        </Menu>
      )}
    </div>
  );
}

/** The tab's content. Shared by the desktop side panel, the phone's bottom sheet and the video page. */
export function PanelBody({ tab }: { tab: PanelTab }) {
  switch (tab) {
    case "summary":
      return <SummaryTab />;
    case "speakers":
      return <SpeakersTab />;
    case "entities":
      return <EntitiesTab />;
    case "chat":
      return <ChatTab />;
    case "history":
      return <HistoryTab />;
    case "metadata":
      return <MetadataTab />;
    case "iiif":
      return <IiifTab />;
    case "details":
      return <DetailsTab />;
    default:
      return null;
  }
}

export function PanelScroll({
  id,
  tab,
  children,
  className,
}: {
  id: string;
  tab: PanelTab;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      id={`${id}-panel`}
      role="tabpanel"
      aria-labelledby={`${id}-tab-${tab}`}
      data-player-keys={tab === "chat" ? "off" : undefined}
      className={cn(
        "flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-5 py-4",
        tab === "chat" && "overflow-hidden p-0",
        className,
      )}
    >
      {children}
    </div>
  );
}

/** Desktop side panel (488px): Summary · Speakers · Entities · Chat · Notes · History, and More (…). */
export function SidePanel() {
  const { tab, setTab } = useRec();
  const current = [...AUDIO_TABS, ...MORE_TABS].some((t) => t.value === tab) ? tab : "summary";
  return (
    <aside aria-label="Recording panels" className="flex min-h-0 min-w-0 flex-col border-l border-border bg-background">
      <PanelTabs tabs={AUDIO_TABS} more={MORE_TABS} value={current} onChange={setTab} idBase="side" />
      <PanelScroll id="side" tab={current}>
        <PanelBody tab={current} />
      </PanelScroll>
    </aside>
  );
}
