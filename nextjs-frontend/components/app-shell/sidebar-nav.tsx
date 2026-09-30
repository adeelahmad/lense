"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { isActive, navFor } from "@/components/app-shell/nav-config";
import { cn } from "@/lib/utils";

export function SidebarNav({ admin }: { admin: boolean }) {
  const pathname = usePathname();
  const sections = navFor(admin);
  return (
    <nav aria-label="Main" className="grid gap-4">
      {sections.map((section, i) => (
        <div key={section.label ?? i} className="grid gap-1">
          {section.label && (
            <p className="px-3 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              {section.label}
            </p>
          )}
          <ul className="grid gap-0.5">
            {section.items.map(({ href, label, icon: Icon }) => {
              const active = isActive(pathname, href);
              return (
                <li key={href}>
                  <Link
                    href={href}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "flex items-center gap-3 rounded-md px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground",
                      active && "bg-accent font-medium text-accent-foreground",
                    )}
                  >
                    <Icon className="h-4 w-4" aria-hidden />
                    {label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}
