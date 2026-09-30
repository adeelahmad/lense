"use client";

import * as M from "@radix-ui/react-dropdown-menu";
import * as P from "@radix-ui/react-popover";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export const Menu = M.Root;
export const MenuTrigger = M.Trigger;

export function MenuContent({ children, align = "end", className }: { children: ReactNode; align?: "start" | "end" | "center"; className?: string }) {
  return (
    <M.Portal>
      <M.Content align={align} sideOffset={6} className={cn("z-[150] min-w-[220px] rounded-md border border-border bg-background p-1.5 shadow-2 animate-fade-in", className)}>
        {children}
      </M.Content>
    </M.Portal>
  );
}

export function MenuItem({
  children,
  onSelect,
  icon,
  shortcut,
  danger,
  disabled,
  asChild,
}: {
  children: ReactNode;
  onSelect?: () => void;
  icon?: ReactNode;
  shortcut?: ReactNode;
  danger?: boolean;
  disabled?: boolean;
  asChild?: boolean;
}) {
  return (
    <M.Item
      asChild={asChild}
      disabled={disabled}
      onSelect={onSelect}
      className={cn(
        "flex h-9 cursor-pointer select-none items-center gap-2.5 rounded-sm px-2.5 text-[13.5px] outline-none data-[disabled]:cursor-not-allowed data-[highlighted]:bg-surface-neutral data-[disabled]:opacity-50 [&_svg]:size-4 [&_svg]:text-fg-secondary",
        danger ? "text-red-dark" : "text-fg",
      )}
    >
      {asChild ? (
        children
      ) : (
        <>
          {icon}
          <span className="flex-1">{children}</span>
          {shortcut && <span className="text-[12px] text-fg-muted">{shortcut}</span>}
        </>
      )}
    </M.Item>
  );
}

export function MenuSeparator() {
  return <M.Separator className="mx-1 my-1.5 h-px bg-border" />;
}

export function MenuLabel({ children }: { children: ReactNode }) {
  return <M.Label className="px-2.5 pb-1 pt-2 label-caps">{children}</M.Label>;
}

export const Popover = P.Root;
export const PopoverTrigger = P.Trigger;
export const PopoverClose = P.Close;
export function PopoverContent({ children, align = "start", className }: { children: ReactNode; align?: "start" | "end" | "center"; className?: string }) {
  return (
    <P.Portal>
      <P.Content align={align} sideOffset={6} className={cn("z-[150] rounded-md border border-border bg-background shadow-2 animate-fade-in outline-none", className)}>
        {children}
      </P.Content>
    </P.Portal>
  );
}
