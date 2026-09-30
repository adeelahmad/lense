"use client";

import { useQuery } from "@tanstack/react-query";
import { Bell, Menu as MenuIcon, Upload } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { Speakers } from "@/app/openapi-client";
import { AccountMenu } from "@/components/app-shell/account-menu";
import { ActivityPill } from "@/components/app-shell/activity";
import { CommandPalette } from "@/components/app-shell/command-palette";
import { NamespaceSwitcher } from "@/components/app-shell/namespace-switcher";
import { Nav } from "@/components/app-shell/nav";
import { Shortcuts } from "@/components/app-shell/shortcuts";
import { Button, IconButton } from "@/components/ui/button";
import { data, useApiClient } from "@/lib/api/browser";
import type { SessionUser } from "@/lib/auth/tokens";
import { useArchive } from "@/lib/hooks/session";

const COLLAPSED = "lens.nav.collapsed";

/** The signed-in frame: nav rail, top bar (namespace, ⌘K, activity, import, alerts, account) and the page. */
export function AppShell({ user, children }: { user: SessionUser; children: ReactNode }) {
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const pathname = usePathname();
  const { namespaces, namespace, can, admin } = useArchive();
  const client = useApiClient();

  useEffect(() => {
    try {
      setCollapsed(localStorage.getItem(COLLAPSED) === "1");
    } catch {
      /* storage unavailable */
    }
  }, []);
  const toggle = () =>
    setCollapsed((c) => {
      try {
        localStorage.setItem(COLLAPSED, c ? "0" : "1");
      } catch {
        /* storage unavailable */
      }
      return !c;
    });
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (
        e.key === "[" &&
        !e.metaKey &&
        !e.ctrlKey &&
        !["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName) &&
        !t.isContentEditable
      )
        toggle();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  useEffect(() => setMobileOpen(false), [pathname]);
  // Recording pages collapse the nav to icons to give the transcript room (Recording R1); the toggle still opens it.
  const autoCollapse = pathname.startsWith("/recordings/");
  const [peek, setPeek] = useState(false);
  useEffect(() => setPeek(false), [pathname]);

  const recordings = namespaces
    .filter((n) => !namespace || n.name === namespace)
    .reduce((a, n) => a + ((n.recordings as number) ?? 0), 0);
  const reviews = useQuery({
    queryKey: ["speaker-reviews", namespace],
    queryFn: async () => {
      const r = await data(Speakers.listSpeakers({ client, query: { ns: namespace as string } }));
      return (r.speakers ?? []).filter((s) => (s.suggestions ?? []).length > 0).length;
    },
    enabled: Boolean(namespace),
    staleTime: 60_000,
  });
  const counts = { recordings, reviews: reviews.data };
  const canImport = can("editor", namespace);

  return (
    <div className="flex min-h-screen bg-background">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[400] focus:rounded-sm focus:bg-background focus:px-3 focus:py-2 focus:shadow-2"
      >
        Skip to content
      </a>
      <div className="sticky top-0 hidden h-screen shrink-0 md:block">
        <Nav
          admin={admin || user.admin}
          collapsed={autoCollapse ? !peek : collapsed}
          onToggle={autoCollapse ? () => setPeek((p) => !p) : toggle}
          counts={counts}
        />
      </div>
      {mobileOpen && (
        <div className="fixed inset-0 z-[120] md:hidden">
          <div className="absolute inset-0 bg-[var(--scrim)]" onClick={() => setMobileOpen(false)} aria-hidden />
          <Nav
            admin={admin || user.admin}
            collapsed={false}
            counts={counts}
            onNavigate={() => setMobileOpen(false)}
            className="relative shadow-3"
          />
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-40 flex h-16 items-center gap-3 border-b border-border bg-background px-3 md:px-4">
          <IconButton label="Open navigation" className="md:hidden" onClick={() => setMobileOpen(true)}>
            <MenuIcon />
          </IconButton>
          <div className="hidden sm:block">
            <NamespaceSwitcher />
          </div>
          <div className="flex min-w-0 flex-1 justify-center px-1">
            <CommandPalette />
          </div>
          <ActivityPill />
          <Button
            asChild={canImport}
            variant="primary"
            size="md"
            disabled={!canImport}
            disabledReason="Importing needs editor access to a namespace"
            className="hidden lg:inline-flex"
            icon={canImport ? undefined : <Upload />}
          >
            {canImport ? (
              <Link href="/import">
                <Upload /> Import
              </Link>
            ) : (
              "Import"
            )}
          </Button>
          <IconButton label="Needs attention" onClick={() => (window.location.href = "/#attention")}>
            <Bell />
          </IconButton>
          <AccountMenu name={user.name} email={user.email} />
        </header>
        <main id="main" className="min-w-0 flex-1">
          {children}
        </main>
      </div>
      <Shortcuts />
    </div>
  );
}
