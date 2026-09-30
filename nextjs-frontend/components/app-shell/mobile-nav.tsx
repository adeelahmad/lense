"use client";

import { Menu } from "lucide-react";
import Link from "next/link";

import { navFor } from "@/components/app-shell/nav-config";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

/** Navigation for small screens, where the sidebar is hidden. */
export function MobileNav({ admin }: { admin: boolean }) {
  const sections = navFor(admin);
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="md:hidden"
          aria-label="Open navigation"
        >
          <Menu className="h-5 w-5" aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-56">
        {sections.map((section, i) => (
          <div key={section.label ?? i}>
            {i > 0 && <DropdownMenuSeparator />}
            {section.label && (
              <DropdownMenuLabel>{section.label}</DropdownMenuLabel>
            )}
            {section.items.map(({ href, label, icon: Icon }) => (
              <DropdownMenuItem key={href} asChild>
                <Link href={href} className="flex items-center gap-2">
                  <Icon className="h-4 w-4" aria-hidden />
                  {label}
                </Link>
              </DropdownMenuItem>
            ))}
          </div>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
