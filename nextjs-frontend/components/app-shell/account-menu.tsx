"use client";

import { useQuery } from "@tanstack/react-query";
import { KeyRound, Keyboard, LogOut, SunMoon, UserRound, Users } from "lucide-react";
import Link from "next/link";
import { signOut } from "next-auth/react";
import { useState } from "react";

import { Tokens } from "@/app/openapi-client";
import { useTheme, type ThemeChoice } from "@/components/app-shell/theme";
import { Menu, MenuContent, MenuItem, MenuTrigger } from "@/components/ui/menu";
import { Avatar, Skeleton } from "@/components/ui/states";
import { data, useApiClient } from "@/lib/api/browser";
import { useArchive } from "@/lib/hooks/session";

const NEXT: Record<ThemeChoice, ThemeChoice> = {
  system: "light",
  light: "dark",
  dark: "system",
};
const ITEM = "h-[38px] rounded-sm text-[14px] font-medium";

/** "Editor in podcasts · Viewer in customer-calls", or the admin line. */
export function rolesSummary(admin: boolean, roles: Record<string, string>): string {
  if (admin) return "Platform admin · owner of every namespace";
  const list = Object.entries(roles);
  if (!list.length) return "No namespaces yet";
  const shown = list.slice(0, 3).map(([ns, r]) => `${r[0].toUpperCase()}${r.slice(1)} in ${ns}`);
  return shown.join(" · ") + (list.length > 3 ? ` · +${list.length - 3} more` : "");
}

/** Name, roles, profile and password, API tokens, appearance, shortcuts, sign out (Access AC4). */
export function AccountMenu({ name, email }: { name?: string | null; email: string }) {
  const { me, admin } = useArchive();
  const client = useApiClient();
  const [open, setOpen] = useState(false);
  const [theme, setTheme] = useTheme();
  const tokens = useQuery({
    queryKey: ["tokens"],
    queryFn: () => data(Tokens.listTokens({ client })),
    enabled: open,
    staleTime: 30_000,
  });
  const roles = (me?.roles ?? {}) as Record<string, string>;
  const owns = !admin && Object.values(roles).includes("owner");
  const display = me?.user.name || name || me?.user.email || email;

  return (
    <Menu open={open} onOpenChange={setOpen}>
      <MenuTrigger className="rounded-full" aria-label="Account menu">
        <Avatar name={display} size={32} className="border border-border hover:ring-2 hover:ring-border" />
      </MenuTrigger>
      <MenuContent className="w-[300px] rounded-[14px] p-2 shadow-3">
        <div className="flex items-center gap-3 border-b border-border px-2.5 pb-3 pt-2.5">
          <Avatar name={display} size={40} className="border border-border" />
          <div className="flex min-w-0 flex-col gap-[3px]">
            <div className="truncate text-[14px] font-bold leading-tight text-fg">{display}</div>
            <div className="truncate text-[12.5px] leading-none text-fg-muted">{me?.user.email || email}</div>
          </div>
        </div>
        <div className="px-2.5 pb-1 pt-2 text-[12px] leading-normal text-fg-secondary">
          {me ? rolesSummary(admin, roles) : <Skeleton className="my-[3px] h-3 w-44" />}
        </div>
        <MenuItem asChild className={ITEM}>
          <Link href="/account">
            <UserRound />
            <span className="flex-1">Profile and password</span>
          </Link>
        </MenuItem>
        <MenuItem asChild className={ITEM}>
          <Link href="/account/tokens">
            <KeyRound />
            <span className="flex-1">API tokens</span>
            {tokens.data && <span className="tabular text-[12px] font-normal text-fg-muted">{tokens.data.length}</span>}
          </Link>
        </MenuItem>
        {owns && (
          <MenuItem asChild className={ITEM}>
            <Link href="/admin/namespaces">
              <Users />
              <span className="flex-1">Namespace members</span>
            </Link>
          </MenuItem>
        )}
        <MenuItem className={ITEM} icon={<SunMoon />} onSelect={() => setTheme(NEXT[theme])}>
          Appearance: {theme[0].toUpperCase() + theme.slice(1)}
        </MenuItem>
        <MenuItem
          className={ITEM}
          icon={<Keyboard />}
          shortcut="?"
          onSelect={() => window.dispatchEvent(new CustomEvent("lens:shortcuts"))}
        >
          Keyboard shortcuts
        </MenuItem>
        <MenuItem className={ITEM} icon={<LogOut />} onSelect={() => void signOut({ redirectTo: "/login" })}>
          Sign out
        </MenuItem>
      </MenuContent>
    </Menu>
  );
}
