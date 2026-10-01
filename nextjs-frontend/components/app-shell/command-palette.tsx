"use client";

import * as D from "@radix-ui/react-dialog";
import { useQuery } from "@tanstack/react-query";
import { CornerDownLeft, FileAudio, Search, Zap } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { Resources } from "@/app/openapi-client";
import { navFor } from "@/components/app-shell/nav-config";
import { data, useApiClient } from "@/lib/api/browser";
import { tc } from "@/lib/format";
import { useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

type Item = {
  group: string;
  label: string;
  meta?: string;
  href: string;
  icon: React.ReactNode;
};

/** ⌘K: jump to a recording or a page; ">" limits results to actions. */
export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const router = useRouter();
  const client = useApiClient();
  const { admin, namespace, can } = useArchive();
  const listRef = useRef<HTMLUListElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const recs = useQuery({
    queryKey: ["palette-recordings", namespace],
    queryFn: () =>
      data(
        Resources.listRecordings({
          client,
          query: { ns: namespace, limit: 1000 },
        }),
      ),
    enabled: open,
    staleTime: 60_000,
  });

  const items = useMemo<Item[]>(() => {
    const term = q.trim().toLowerCase();
    const actionsOnly = term.startsWith(">");
    const t = actionsOnly ? term.slice(1).trim() : term;
    const match = (s: string) => !t || s.toLowerCase().includes(t);
    const actions: Item[] = [
      ...(can("editor")
        ? [
            {
              group: "Actions",
              label: "Import recordings or transcripts",
              href: "/import",
              icon: <Zap className="size-4" />,
            },
          ]
        : []),
      {
        group: "Actions",
        label: "Open Activity",
        href: "/activity",
        icon: <Zap className="size-4" />,
      },
      {
        group: "Actions",
        label: "API tokens",
        href: "/account/tokens",
        icon: <Zap className="size-4" />,
      },
    ].filter((a) => match(a.label));
    if (actionsOnly) return actions;
    const pages: Item[] = navFor(admin)
      .filter((n) => match(n.label))
      .map((n) => ({
        group: "Pages",
        label: n.label,
        href: n.href,
        icon: <n.icon className="size-4" />,
      }));
    const recordings: Item[] = t
      ? (recs.data ?? [])
          .filter((r) => match(r.title ?? ""))
          .slice(0, 8)
          .map((r) => ({
            group: "Recordings",
            label: r.title ?? `Recording ${r.id}`,
            meta: [r.namespace, r.duration_ms ? tc(r.duration_ms) : null].filter(Boolean).join(" · "),
            href: `/resources/${r.id}`,
            icon: <FileAudio className="size-4" />,
          }))
      : [];
    return [...recordings, ...pages, ...actions];
  }, [q, recs.data, admin, can]);

  useEffect(() => setSel(0), [q]);
  const go = (it: Item | undefined) => {
    if (!it) return;
    setOpen(false);
    setQ("");
    router.push(it.href);
  };

  let lastGroup = "";
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="flex h-10 w-full max-w-[540px] items-center gap-2.5 rounded-sm bg-surface-neutral px-3.5 text-left text-[14px] text-fg-muted hover:bg-border/60"
      >
        <Search className="size-4 shrink-0" />
        <span className="flex-1 truncate">Jump to a recording, speaker or page…</span>
        <span className="hidden gap-1 sm:flex">
          <kbd className="grid h-5 min-w-5 place-items-center rounded-xs border border-border bg-background px-1 font-sans text-[11px]">
            ⌘
          </kbd>
          <kbd className="grid h-5 min-w-5 place-items-center rounded-xs border border-border bg-background px-1 font-sans text-[11px]">
            K
          </kbd>
        </span>
      </button>
      <D.Root open={open} onOpenChange={setOpen}>
        <D.Portal>
          <D.Overlay className="fixed inset-0 z-[100] bg-[var(--scrim)] backdrop-blur-[2px]" />
          <D.Content
            className="fixed left-1/2 top-[12vh] z-[101] w-[calc(100vw-32px)] max-w-[600px] -translate-x-1/2 overflow-hidden rounded-lg bg-background shadow-3"
            aria-describedby={undefined}
          >
            <D.Title className="sr-only">Jump to</D.Title>
            <div className="flex items-center gap-3 border-b border-border px-4">
              <Search className="size-4 text-fg-muted" />
              <input
                autoFocus
                value={q}
                onChange={(e) => setQ(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "ArrowDown") {
                    e.preventDefault();
                    setSel((s) => Math.min(items.length - 1, s + 1));
                  } else if (e.key === "ArrowUp") {
                    e.preventDefault();
                    setSel((s) => Math.max(0, s - 1));
                  } else if (e.key === "Enter") {
                    e.preventDefault();
                    const it = items[sel];
                    if (it && (e.metaKey || e.ctrlKey)) window.open(it.href, "_blank");
                    else go(it);
                  }
                }}
                role="combobox"
                aria-expanded
                aria-controls="palette-list"
                aria-activedescendant={items[sel] ? `palette-${sel}` : undefined}
                placeholder="Search recordings and pages, or > for actions"
                className="h-14 flex-1 bg-transparent text-[16px] text-fg outline-none placeholder:text-fg-muted"
              />
              <kbd className="rounded-xs border border-border px-1.5 text-[11px] text-fg-muted">esc</kbd>
            </div>
            <ul id="palette-list" ref={listRef} role="listbox" className="max-h-[50vh] overflow-y-auto p-2">
              {items.length === 0 && (
                <li className="px-3 py-6 text-center text-[13.5px] text-fg-secondary">
                  {recs.isLoading ? "Searching…" : "No matches"}
                </li>
              )}
              {items.map((it, i) => {
                const header = it.group !== lastGroup ? it.group : null;
                lastGroup = it.group;
                return (
                  <li key={`${it.group}-${it.href}-${i}`} role="presentation">
                    {header && <div className="px-2.5 pb-1 pt-2 label-caps">{header}</div>}
                    <button
                      id={`palette-${i}`}
                      type="button"
                      role="option"
                      aria-selected={i === sel}
                      onMouseMove={() => setSel(i)}
                      onClick={() => go(it)}
                      className={cn(
                        "flex w-full items-center gap-3 rounded-sm px-2.5 py-2 text-left text-[14px] text-fg",
                        i === sel && "bg-hl",
                      )}
                    >
                      <span className="text-fg-secondary">{it.icon}</span>
                      <span className="min-w-0 flex-1 truncate">{it.label}</span>
                      {it.meta && <span className="shrink-0 text-[12.5px] text-fg-muted">{it.meta}</span>}
                      {i === sel && <CornerDownLeft className="size-3.5 text-fg-muted" />}
                    </button>
                  </li>
                );
              })}
            </ul>
            <div className="flex gap-4 border-t border-border px-4 py-2 text-[12px] text-fg-muted">
              <span>↑↓ move</span>
              <span>↵ open</span>
              <span>⌘↵ in new tab</span>
              <span>&gt; actions only</span>
            </div>
          </D.Content>
        </D.Portal>
      </D.Root>
    </>
  );
}
