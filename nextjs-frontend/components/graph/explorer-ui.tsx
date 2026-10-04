"use client";

import * as M from "@radix-ui/react-dropdown-menu";
import {
  ArrowDownToLine,
  ArrowUpToLine,
  EyeOff,
  GitFork,
  Loader2,
  Network,
  Route,
  RotateCcw,
  Share2,
  SquareArrowOutUpRight,
  Waypoints,
  X,
} from "lucide-react";
import Link from "next/link";

import { RELATIONS, type Explorer, type Relation } from "@/components/graph/explorer";
import { LAYOUTS, type LayoutKind } from "@/components/graph/layouts";
import { nodeHref, type GraphNode } from "@/components/graph/model";
import { MenuContent, MenuItem, MenuLabel, MenuSeparator } from "@/components/ui/menu";
import { cn } from "@/lib/utils";

const ICONS: Record<Relation, typeof Network> = {
  parents: ArrowUpToLine,
  children: ArrowDownToLine,
  ancestors: ArrowUpToLine,
  descendants: ArrowDownToLine,
  neighbours: Share2,
};

/** A node's menu (right-click, long press, or M on the keyboard), opened at a point on the canvas. */
export function NodeMenu({
  node,
  at,
  ex,
  byId,
  onClose,
  onSelect,
}: {
  node: GraphNode | null;
  at: { x: number; y: number } | null;
  ex: Explorer;
  byId: Map<string, GraphNode>;
  onClose: () => void;
  onSelect: (id: string) => void;
}) {
  const start = ex.pathStart ? byId.get(ex.pathStart) : null;
  const href = node ? nodeHref(node) : null;
  return (
    <M.Root open={Boolean(node && at)} onOpenChange={(o) => !o && onClose()} modal={false}>
      <M.Trigger asChild>
        <span
          aria-hidden
          className="pointer-events-none absolute size-px"
          style={{ left: at?.x ?? 0, top: at?.y ?? 0 }}
        />
      </M.Trigger>
      {node && (
        <MenuContent
          align="start"
          className="max-h-[70dvh] overflow-y-auto"
          // opened by a right-click or long press on the canvas, which then takes focus: that isn't leaving the menu
          onFocusOutside={(e) => e.preventDefault()}
        >
          <MenuLabel>{node.label}</MenuLabel>
          {RELATIONS.map((r) => {
            const Icon = ICONS[r.key];
            return (
              <MenuItem key={r.key} icon={<Icon />} onSelect={() => ex.explore(node, r.key)}>
                {r.label}
              </MenuItem>
            );
          })}
          <MenuSeparator />
          {start && start.id !== node.id ? (
            <MenuItem icon={<Waypoints />} onSelect={() => ex.paths(start, node)}>
              Paths from {start.label.length > 18 ? `${start.label.slice(0, 17)}…` : start.label}
            </MenuItem>
          ) : (
            <MenuItem icon={<Waypoints />} onSelect={() => ex.setPathStart(node.id)}>
              Find paths from here…
            </MenuItem>
          )}
          <MenuItem icon={<Route />} onSelect={() => ex.addStop(node.id)}>
            {ex.route.length ? `Add to route (stop ${ex.route.length + 1})` : "Start a route here"}
          </MenuItem>
          <MenuSeparator />
          <MenuItem icon={<GitFork />} onSelect={() => ex.setLayout("bfs", node.id)}>
            BFS tree from here
          </MenuItem>
          <MenuItem icon={<Network />} onSelect={() => ex.setLayout("dfs", node.id)}>
            DFS tree from here
          </MenuItem>
          <MenuItem icon={<Share2 />} onSelect={() => ex.setLayout("radial", node.id)}>
            Rings around this
          </MenuItem>
          <MenuSeparator />
          <MenuItem onSelect={() => onSelect(node.id)}>Show details</MenuItem>
          {href && (
            <MenuItem asChild>
              <Link href={href} className="flex items-center gap-2.5">
                <SquareArrowOutUpRight /> Open its page
              </Link>
            </MenuItem>
          )}
          <MenuItem icon={<EyeOff />} onSelect={() => ex.hide(node.id)}>
            Hide from the canvas
          </MenuItem>
        </MenuContent>
      )}
    </M.Root>
  );
}

/** Layout buttons, reset, and what is lit up (an explored set, paths, a route), over the canvas. */
export function ExplorerBar({
  ex,
  byId,
  version,
  asOf,
  onNow,
}: {
  ex: Explorer;
  byId: Map<string, GraphNode>;
  /** the version picker, at the end of the toolbar */
  version?: React.ReactNode;
  /** the version shown, when it isn't now */
  asOf?: string | null;
  onNow?: () => void;
}) {
  const options = LAYOUTS.filter((l) => l.key !== "route" || ex.route.length > 1);
  const label = (id: string) => byId.get(id)?.label ?? id;
  return (
    <div className="pointer-events-none absolute inset-x-3 top-3 z-10 flex flex-col items-start gap-2">
      <div
        role="toolbar"
        aria-label="Layout"
        className="pointer-events-auto flex max-w-full flex-wrap items-center gap-1 rounded-[10px] border border-border bg-background p-1 shadow-1"
      >
        {options.map((l) => (
          <button
            key={l.key}
            type="button"
            title={l.hint}
            aria-pressed={ex.layout === l.key}
            onClick={() => ex.setLayout(l.key as LayoutKind)}
            className={cn(
              "h-8 rounded-[7px] px-2.5 text-[12.5px] font-semibold",
              ex.layout === l.key ? "bg-blue text-white" : "text-fg hover:bg-surface-neutral",
            )}
          >
            {l.label}
          </button>
        ))}
        <span className="mx-0.5 h-5 w-px bg-border" />
        <button
          type="button"
          onClick={ex.reset}
          title="Back to the overview: force layout, nothing added, nothing moved"
          className="inline-flex h-8 items-center gap-1.5 rounded-[7px] px-2.5 text-[12.5px] font-semibold text-fg hover:bg-surface-neutral [&_svg]:size-3.5"
        >
          <RotateCcw /> Reset
        </button>
        {version && (
          <>
            <span className="mx-0.5 h-5 w-px bg-border" />
            {version}
          </>
        )}
        {ex.busy && (
          <span className="inline-flex items-center gap-1.5 px-2 text-[12px] text-fg-muted" aria-live="polite">
            <Loader2 className="size-3.5 animate-spin" /> {ex.busy}…
          </span>
        )}
      </div>
      {asOf && onNow && (
        <Chip onClear={onNow} label="Back to now">
          As of {/^\d+$/.test(asOf) ? `version ${asOf}` : `“${asOf}”`}; mentions are today’s
        </Chip>
      )}
      {ex.highlight && (
        <Chip onClear={ex.clearHighlight} label="Clear highlight">
          {ex.highlight.title}
        </Chip>
      )}
      {ex.pathStart && (
        <Chip onClear={() => ex.setPathStart(null)} label="Cancel">
          Paths from {label(ex.pathStart)}: now pick where to
        </Chip>
      )}
      {ex.route.length > 0 && (
        <div className="pointer-events-auto flex max-w-full flex-wrap items-center gap-1.5 rounded-[10px] border border-blue-border bg-blue-surface px-2.5 py-1.5 text-[12.5px] text-fg">
          <Route className="size-3.5 text-blue" aria-hidden />
          <span className="font-semibold">Route</span>
          {ex.route.map((id, i) => (
            <span key={`${id}-${i}`} className="inline-flex items-center gap-1">
              {i > 0 && <span className="text-fg-muted">→</span>}
              <button
                type="button"
                onClick={() => ex.removeStop(i)}
                aria-label={`Remove ${label(id)} from the route`}
                className="rounded-pill border border-border bg-background px-2 py-0.5 hover:border-red-border"
              >
                {label(id)} ×
              </button>
            </span>
          ))}
          {ex.route.length < 2 && <span className="text-fg-muted">add another stop from a node’s menu</span>}
          <button
            type="button"
            onClick={ex.clearRoute}
            aria-label="Clear the route"
            className="ml-1 grid size-6 place-items-center rounded-full hover:bg-background"
          >
            <X className="size-3.5" />
          </button>
        </div>
      )}
    </div>
  );
}

function Chip({ children, onClear, label }: { children: React.ReactNode; onClear: () => void; label: string }) {
  return (
    <div className="pointer-events-auto flex max-w-full items-center gap-2 rounded-pill border border-border bg-background py-1 pl-3 pr-1 text-[12.5px] font-medium text-fg shadow-1">
      <span className="min-w-0 truncate">{children}</span>
      <button
        type="button"
        onClick={onClear}
        aria-label={label}
        className="grid size-6 shrink-0 place-items-center rounded-full hover:bg-surface-neutral"
      >
        <X className="size-3.5" />
      </button>
    </div>
  );
}
