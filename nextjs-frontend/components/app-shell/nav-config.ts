import {
  AudioLines,
  CalendarClock,
  ChartNoAxesColumn,
  HardDriveDownload,
  House,
  LibraryBig,
  type LucideIcon,
  MessagesSquare,
  Search,
  Settings,
  Shield,
  Waypoints,
  Workflow,
} from "lucide-react";

export type NavItem = {
  label: string;
  href: string;
  icon: LucideIcon;
  /** Only shown to archive administrators. */
  adminOnly?: boolean;
  /** A count shown next to the label (library size, speakers to review). */
  countKey?: "recordings" | "reviews";
  /** Draw a divider after this item. */
  divider?: boolean;
};

/** Primary navigation, in the design's order (Nav.dc.html). */
export const NAV: NavItem[] = [
  { label: "Home", href: "/", icon: House },
  {
    label: "Library",
    href: "/library",
    icon: LibraryBig,
    countKey: "recordings",
  },
  { label: "Search", href: "/search", icon: Search },
  { label: "Chat", href: "/chat", icon: MessagesSquare },
  {
    label: "Speakers",
    href: "/speakers",
    icon: AudioLines,
    countKey: "reviews",
  },
  { label: "Graph", href: "/graph", icon: Waypoints },
  {
    label: "Reports",
    href: "/reports",
    icon: ChartNoAxesColumn,
    divider: true,
  },
  { label: "Pipelines", href: "/pipelines", icon: Workflow },
  { label: "Routines", href: "/routines", icon: CalendarClock, adminOnly: true },
  { label: "Sources", href: "/sources", icon: HardDriveDownload },
  { label: "Settings", href: "/settings", icon: Settings, adminOnly: true },
  { label: "Admin", href: "/admin", icon: Shield, adminOnly: true },
];

export function navFor(admin: boolean): NavItem[] {
  return NAV.filter((item) => admin || !item.adminOnly);
}

export function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  if (href === "/library")
    return (
      pathname === "/library" ||
      pathname.startsWith("/library/") ||
      pathname.startsWith("/resources/") ||
      pathname.startsWith("/recordings/")
    );
  // Workflows, content types and templates are tabs of Pipelines.
  if (
    href === "/pipelines" &&
    ["/templates", "/workflows", "/content-types"].some((p) => pathname === p || pathname.startsWith(`${p}/`))
  )
    return true;
  return pathname === href || pathname.startsWith(`${href}/`);
}
