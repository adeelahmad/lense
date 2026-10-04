import {
  Blocks,
  CalendarClock,
  ChartNoAxesColumn,
  HardDriveDownload,
  House,
  LibraryBig,
  type LucideIcon,
  MessagesSquare,
  NotebookPen,
  RadioTower,
  Search,
  Settings,
  Shapes,
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
  /** Only shown to people who aren't administrators (admins have it under another item). */
  membersOnly?: boolean;
  /** A count shown next to the label (library size, speakers to review in Settings). */
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
  { label: "Notes", href: "/notes", icon: NotebookPen },
  { label: "Entities", href: "/entities", icon: Shapes },
  { label: "Graph", href: "/graph", icon: Waypoints },
  {
    label: "Reports",
    href: "/reports",
    icon: ChartNoAxesColumn,
    divider: true,
  },
  { label: "Pipelines", href: "/pipelines", icon: Workflow },
  { label: "Routines", href: "/routines", icon: CalendarClock, adminOnly: true },
  { label: "Extensions", href: "/extensions", icon: Blocks },
  // Admins see every source as a sensor; members see the watched folders feeding their namespaces.
  { label: "Sensors", href: "/sensors", icon: RadioTower, adminOnly: true },
  { label: "Sources", href: "/sources", icon: HardDriveDownload, membersOnly: true },
  // Everyone has Settings: members find Speakers there, admins the archive's settings too.
  { label: "Settings", href: "/settings", icon: Settings, countKey: "reviews" },
  { label: "Admin", href: "/admin", icon: Shield, adminOnly: true },
];

export function navFor(admin: boolean): NavItem[] {
  return NAV.filter((item) => (admin ? !item.membersOnly : !item.adminOnly));
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
  // Sources (files, email, calendars) are a tab of Sensors.
  if (href === "/sensors" && (pathname === "/sources" || pathname.startsWith("/sources/"))) return true;
  // Members opening /sensors see their Sources page.
  if (href === "/sources" && pathname === "/sensors") return true;
  // Speakers live in Settings; a speaker's profile keeps its own address.
  if (href === "/settings" && (pathname === "/speakers" || pathname.startsWith("/speakers/"))) return true;
  return pathname === href || pathname.startsWith(`${href}/`);
}
