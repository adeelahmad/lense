import {
  AlarmClock,
  Blocks,
  ChartLine,
  ChartNoAxesColumn,
  HardDriveDownload,
  History,
  House,
  Inbox,
  LibraryBig,
  MessagesSquare,
  NotebookPen,
  Podcast,
  RadioTower,
  Rss,
  Search,
  Settings,
  Shapes,
  Shield,
  Tags,
  type LucideIcon,
  Wallet,
  Waypoints,
  Workflow,
  Zap,
} from "lucide-react";

export type NavItem = {
  label: string;
  href: string;
  icon: LucideIcon;
  /** Only shown to archive administrators. */
  adminOnly?: boolean;
  /** Only shown to people who aren't administrators (admins have it under another item). */
  membersOnly?: boolean;
  /** A count shown next to the label (library size, speakers to review in Settings, approvals waiting). */
  countKey?: "recordings" | "reviews" | "approvals" | "feeds";
  /** Shown only while its count is above zero, or while its page is open, so the nav of a Lens with nothing there
   * stays as short as it was (on a phone, every item more pushes the ones below off the screen). */
  whileCounted?: boolean;
  /** The group it's in, named above its first item; the first group (what people use every day) has no name. */
  group: NavGroup;
};

export type NavGroup = "" | "Explore" | "Automate" | "Manage";

/** Primary navigation, in the design's order (Nav.dc.html), in groups: what people use every day, what Lens found in
 * it, what runs by itself, and running Lens. */
export const NAV: NavItem[] = [
  { label: "Home", href: "/", icon: House, group: "" },
  {
    label: "Library",
    href: "/library",
    icon: LibraryBig,
    countKey: "recordings",
    group: "",
  },
  { label: "Search", href: "/search", icon: Search, group: "" },
  { label: "Chat", href: "/chat", icon: MessagesSquare, group: "" },
  { label: "Notes", href: "/notes", icon: NotebookPen, group: "" },
  { label: "Timeline", href: "/timeline", icon: History, group: "Explore" },
  { label: "Entities", href: "/entities", icon: Shapes, group: "Explore" },
  { label: "Topics", href: "/topics", icon: Tags, group: "Explore" },
  { label: "Graph", href: "/graph", icon: Waypoints, group: "Explore" },
  { label: "Podcasts", href: "/podcasts", icon: Podcast, group: "Explore" },
  // in the nav once there's a feed (Podcasts' Feeds button and Jump to lead there before that)
  { label: "Feeds", href: "/feeds", icon: Rss, countKey: "feeds", whileCounted: true, group: "Explore" },
  { label: "Reports", href: "/reports", icon: ChartNoAxesColumn, group: "Explore" },
  // what waits for your pick: in the nav while anything does; the top bar's badge and Home's row lead there too
  {
    label: "Approvals",
    href: "/approvals",
    icon: Inbox,
    countKey: "approvals",
    whileCounted: true,
    group: "Automate",
  },
  { label: "Automations", href: "/automations", icon: Zap, group: "Automate" },
  { label: "Pipelines", href: "/pipelines", icon: Workflow, group: "Automate" },
  // Everything that runs on a clock in one list: everyone has it, for the schedules of their own automations. It
  // takes the row Routines had (the Routines page opens from it), so the nav fits a phone screen as before.
  { label: "Schedules", href: "/schedules", icon: AlarmClock, group: "Automate" },
  { label: "Extensions", href: "/extensions", icon: Blocks, group: "Automate" },
  // Admins see every source as a sensor; members see the watched folders feeding their namespaces.
  { label: "Sensors", href: "/sensors", icon: RadioTower, adminOnly: true, group: "Automate" },
  { label: "Sources", href: "/sources", icon: HardDriveDownload, membersOnly: true, group: "Automate" },
  { label: "Costs", href: "/costs", icon: Wallet, group: "Manage" },
  { label: "Analytics", href: "/analytics", icon: ChartLine, adminOnly: true, group: "Manage" },
  // Everyone has Settings: members find Speakers there, admins the archive's settings too.
  { label: "Settings", href: "/settings", icon: Settings, countKey: "reviews", group: "Manage" },
  { label: "Admin", href: "/admin", icon: Shield, adminOnly: true, group: "Manage" },
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
  // An entity's page is under /wiki, by name.
  if (href === "/entities" && pathname.startsWith("/wiki/")) return true;
  // The Routines page (its list, proposed graph changes and history) opens from Schedules.
  if (href === "/schedules" && (pathname === "/routines" || pathname.startsWith("/routines/"))) return true;
  // Speakers live in Settings; a speaker's profile keeps its own address.
  if (href === "/settings" && (pathname === "/speakers" || pathname.startsWith("/speakers/"))) return true;
  return pathname === href || pathname.startsWith(`${href}/`);
}
