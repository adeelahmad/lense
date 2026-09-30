import type { ReactNode } from "react";

import { MobileNav } from "@/components/app-shell/mobile-nav";
import { SidebarNav } from "@/components/app-shell/sidebar-nav";
import { UserMenu } from "@/components/app-shell/user-menu";
import { Brand } from "@/components/brand";
import type { SessionUser } from "@/lib/auth/tokens";

/**
 * The signed-in frame: sidebar (brand + nav), top bar (mobile nav, page-level
 * `actions` slot, user menu) and the main content area.
 */
export function AppShell({
  user,
  actions,
  children,
}: {
  user: SessionUser;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="flex min-h-screen bg-background">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-background focus:px-3 focus:py-2 focus:shadow"
      >
        Skip to content
      </a>
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col gap-6 border-r bg-muted/40 px-3 py-4 md:flex">
        <Brand className="px-3" />
        <SidebarNav admin={user.admin} />
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-10 flex h-14 items-center gap-2 border-b bg-background/95 px-4 backdrop-blur">
          <MobileNav admin={user.admin} />
          <Brand className="md:hidden" />
          <div className="ml-auto flex items-center gap-2">
            {actions}
            <UserMenu user={user} />
          </div>
        </header>
        <main id="main" className="flex-1 p-4 md:p-8">
          {children}
        </main>
      </div>
    </div>
  );
}
