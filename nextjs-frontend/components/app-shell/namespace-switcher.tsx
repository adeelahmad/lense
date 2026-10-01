"use client";

import { Check, ChevronsUpDown, Folder, Layers } from "lucide-react";
import { useState } from "react";

import { RoleChip } from "@/components/ui/badge";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/menu";
import { count } from "@/lib/format";
import { useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

/** "SOME": the chip of a namespace someone sees only some collections of. */
function SomeChip({ className }: { className?: string }) {
  return (
    <span
      title="You see some of its collections"
      className={cn(
        "inline-flex h-[22px] items-center rounded-pill border border-border bg-background px-2 text-[11px] font-bold uppercase tracking-[.04em] text-fg-secondary",
        className,
      )}
    >
      Some
    </span>
  );
}

/** The top bar's namespace picker: the namespaces the person has a role in, and those they see some collections of.
 * Others don't exist for them. */
export function NamespaceSwitcher() {
  const { namespaces: full, partialNamespaces, namespace, setNamespace, roleIn, isPartial } = useArchive();
  const namespaces = [...full, ...partialNamespaces].sort((a, b) => a.name.localeCompare(b.name));
  const [open, setOpen] = useState(false);
  const role = roleIn(namespace);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        className="flex h-10 min-w-0 items-center gap-2.5 rounded-sm border border-border bg-background pl-3 pr-2.5 text-left hover:bg-surface"
        aria-label={`Namespace: ${namespace ?? "all namespaces"}. Change`}
      >
        {namespace ? (
          <Folder className="size-4 shrink-0 text-fg-secondary" />
        ) : (
          <Layers className="size-4 shrink-0 text-fg-secondary" />
        )}
        <span className="flex min-w-0 flex-col">
          <span className="text-[9.5px] font-bold uppercase leading-none tracking-[.06em] text-fg-muted">
            Namespace
          </span>
          <span className="truncate text-[14px] font-bold leading-tight text-fg">{namespace ?? "All namespaces"}</span>
        </span>
        {role && <RoleChip role={role} className="hidden lg:inline-flex" />}
        {isPartial(namespace) && <SomeChip className="hidden lg:inline-flex" />}
        <ChevronsUpDown className="size-4 shrink-0 text-fg-muted" />
      </PopoverTrigger>
      <PopoverContent className="w-[300px] p-1.5">
        <ul role="listbox" aria-label="Namespaces">
          {[null, ...namespaces.map((n) => n.name)].map((name) => {
            const ns = namespaces.find((n) => n.name === name);
            const on = name === namespace;
            return (
              <li key={name ?? "*"}>
                <button
                  type="button"
                  role="option"
                  aria-selected={on}
                  onClick={() => {
                    setNamespace(name);
                    setOpen(false);
                  }}
                  className={cn(
                    "flex w-full items-center gap-2.5 rounded-sm px-2.5 py-2 text-left hover:bg-surface-neutral",
                    on && "bg-hl",
                  )}
                >
                  {name ? (
                    <Folder className="size-4 text-fg-secondary" />
                  ) : (
                    <Layers className="size-4 text-fg-secondary" />
                  )}
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[14px] font-semibold text-fg">{name ?? "All namespaces"}</span>
                    <span className="block text-[12px] text-fg-muted">
                      {name ? `${count(ns?.recordings as number)} recordings` : `${namespaces.length} you can see`}
                    </span>
                  </span>
                  {name && (isPartial(name) ? <SomeChip /> : <RoleChip role={roleIn(name)} />)}
                  {on && <Check className="size-4 text-blue" />}
                </button>
              </li>
            );
          })}
        </ul>
        {namespaces.length === 0 && (
          <p className="px-3 py-4 text-[13px] text-fg-secondary">
            You don’t have a role in any namespace yet. Ask an admin to add you.
          </p>
        )}
      </PopoverContent>
    </Popover>
  );
}
