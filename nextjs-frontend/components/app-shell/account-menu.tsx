"use client";

import { KeyRound, Keyboard, LogOut, Monitor, Moon, Sun, UserRound } from "lucide-react";
import Link from "next/link";
import { signOut } from "next-auth/react";

import { useTheme, type ThemeChoice } from "@/components/app-shell/theme";
import { Menu, MenuContent, MenuItem, MenuSeparator, MenuTrigger } from "@/components/ui/menu";
import { Avatar } from "@/components/ui/states";
import { useArchive } from "@/lib/hooks/session";

const NEXT: Record<ThemeChoice, ThemeChoice> = { system: "light", light: "dark", dark: "system" };
const THEME_ICON = { system: Monitor, light: Sun, dark: Moon };

/** Name, roles summary, profile, API tokens, appearance, shortcuts, sign out (Access AC4). */
export function AccountMenu({ name, email }: { name?: string | null; email: string }) {
  const { me, admin } = useArchive();
  const [theme, setTheme] = useTheme();
  const roles = Object.entries(me?.roles ?? {});
  const summary = admin
    ? "Admin · every namespace"
    : roles.length
      ? roles
          .slice(0, 3)
          .map(([ns, r]) => `${r[0].toUpperCase()}${r.slice(1)} in ${ns}`)
          .join(" · ") + (roles.length > 3 ? ` · +${roles.length - 3}` : "")
      : "No namespaces yet";
  const ThemeIcon = THEME_ICON[theme];
  return (
    <Menu>
      <MenuTrigger className="rounded-full" aria-label="Account menu">
        <Avatar name={name || email} size={32} className="hover:ring-2 hover:ring-border" />
      </MenuTrigger>
      <MenuContent className="w-[280px]">
        <div className="flex items-center gap-3 px-2.5 pb-2 pt-1.5">
          <Avatar name={name || email} size={40} />
          <div className="min-w-0">
            <div className="truncate text-[14px] font-bold text-fg">{name || email}</div>
            <div className="truncate text-[12.5px] text-fg-secondary">{email}</div>
          </div>
        </div>
        <p className="px-2.5 pb-2 text-[12px] leading-snug text-fg-muted">{summary}</p>
        <MenuSeparator />
        <MenuItem asChild>
          <Link href="/account">
            <UserRound />
            <span className="flex-1">Profile and password</span>
          </Link>
        </MenuItem>
        <MenuItem asChild>
          <Link href="/account/tokens">
            <KeyRound />
            <span className="flex-1">API tokens</span>
          </Link>
        </MenuItem>
        <MenuItem icon={<ThemeIcon />} onSelect={() => setTheme(NEXT[theme])}>
          Appearance: {theme[0].toUpperCase() + theme.slice(1)}
        </MenuItem>
        <MenuItem icon={<Keyboard />} shortcut="?" onSelect={() => window.dispatchEvent(new CustomEvent("lens:shortcuts"))}>
          Keyboard shortcuts
        </MenuItem>
        <MenuSeparator />
        <MenuItem icon={<LogOut />} onSelect={() => void signOut({ redirectTo: "/login" })}>
          Sign out
        </MenuItem>
      </MenuContent>
    </Menu>
  );
}
