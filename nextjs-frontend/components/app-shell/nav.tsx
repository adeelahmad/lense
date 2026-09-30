"use client";

import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { isActive, navFor } from "@/components/app-shell/nav-config";
import { Brand } from "@/components/brand";
import { Tooltip } from "@/components/ui/tooltip";
import { count } from "@/lib/format";
import { cn } from "@/lib/utils";

/** The primary nav rail: 224px open, 60px collapsed (icons with tooltips). */
export function Nav({
  admin,
  collapsed,
  onToggle,
  counts,
  onNavigate,
  className,
}: {
  admin: boolean;
  collapsed: boolean;
  onToggle?: () => void;
  counts: { recordings?: number; reviews?: number };
  onNavigate?: () => void;
  className?: string;
}) {
  const pathname = usePathname();
  return (
    <nav
      aria-label="Primary"
      className={cn("flex h-full flex-col gap-0.5 border-r border-border bg-background px-2.5 py-3.5", collapsed ? "w-[60px]" : "w-[224px]", className)}
    >
      <div className={cn("flex h-7 items-center px-2 pb-4 pt-1", collapsed && "justify-center px-0")}>
        <Brand collapsed={collapsed} />
      </div>
      {navFor(admin).map((it) => {
        const on = isActive(pathname, it.href);
        const n = it.countKey ? counts[it.countKey] : undefined;
        const link = (
          <Link
            href={it.href}
            onClick={onNavigate}
            aria-current={on ? "page" : undefined}
            className={cn(
              "flex h-[34px] items-center gap-[11px] whitespace-nowrap rounded-sm px-2.5 text-[14px] transition-colors duration-fast",
              on ? "bg-blue-surface font-bold text-fg-accent" : "font-medium text-fg-strong hover:bg-surface-neutral",
              collapsed && "justify-center px-0",
            )}
          >
            <it.icon className="size-[17px] shrink-0" strokeWidth={2} aria-hidden />
            {collapsed ? (
              <span className="sr-only">{it.label}</span>
            ) : (
              <>
                <span className="flex-1">{it.label}</span>
                {n ? <span className="tabular text-[11px] font-medium text-fg-muted">{count(n)}</span> : null}
              </>
            )}
          </Link>
        );
        return (
          <div key={it.href}>
            {collapsed ? (
              <Tooltip content={it.label} side="right">
                {link}
              </Tooltip>
            ) : (
              link
            )}
            {it.divider && <div className="mx-1.5 my-2 h-px bg-border" />}
          </div>
        );
      })}
      <div className="flex-1" />
      {onToggle && (
        <button
          type="button"
          onClick={onToggle}
          aria-label={collapsed ? "Expand navigation" : "Collapse navigation"}
          className={cn("flex h-[34px] items-center gap-[11px] rounded-sm px-2.5 text-[13px] font-medium text-fg-muted hover:bg-surface-neutral", collapsed && "justify-center px-0")}
        >
          {collapsed ? <PanelLeftOpen className="size-[17px]" /> : <PanelLeftClose className="size-[17px]" />}
          {!collapsed && (
            <>
              <span className="flex-1 text-left">Collapse</span>
              <kbd className="font-mono text-[11px]">[</kbd>
            </>
          )}
        </button>
      )}
    </nav>
  );
}
