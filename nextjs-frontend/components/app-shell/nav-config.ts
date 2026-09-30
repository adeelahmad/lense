import {
  Activity,
  FolderOpen,
  LayoutDashboard,
  Library,
  type LucideIcon,
  MessageSquare,
  Network,
  Search,
  Settings,
  Users,
} from "lucide-react";

export type NavItem = {
  label: string;
  href: string;
  icon: LucideIcon;
  /** Only shown to archive administrators. */
  adminOnly?: boolean;
};

export type NavSection = { label?: string; items: NavItem[] };

/**
 * The app's primary navigation. Sections without a page yet land on the
 * "not built yet" page inside the shell.
 */
export const NAV: NavSection[] = [
  {
    items: [
      { label: "Overview", href: "/", icon: LayoutDashboard },
      { label: "Library", href: "/recordings", icon: Library },
      { label: "Search", href: "/search", icon: Search },
    ],
  },
  {
    label: "Explore",
    items: [
      { label: "Speakers", href: "/speakers", icon: Users },
      { label: "Entities", href: "/entities", icon: Network },
      { label: "Collections", href: "/collections", icon: FolderOpen },
      { label: "Chat", href: "/chat", icon: MessageSquare },
    ],
  },
  {
    label: "Manage",
    items: [
      { label: "Jobs", href: "/jobs", icon: Activity },
      { label: "Settings", href: "/settings", icon: Settings, adminOnly: true },
    ],
  },
];

export function navFor(admin: boolean): NavSection[] {
  return NAV.map((section) => ({
    ...section,
    items: section.items.filter((item) => admin || !item.adminOnly),
  })).filter((section) => section.items.length > 0);
}

export function isActive(pathname: string, href: string): boolean {
  return href === "/"
    ? pathname === "/"
    : pathname === href || pathname.startsWith(`${href}/`);
}
