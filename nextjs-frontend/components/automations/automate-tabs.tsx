"use client";

import { Plus } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Tabs } from "@/components/ui/tabs";
import { useArchive } from "@/lib/hooks/session";

/** Automations and Schedules share one row in the nav (it fits a phone screen), so each page has both as tabs. */
export function AutomateTabs({ tab }: { tab: "automations" | "schedules" }) {
  return (
    <Tabs
      aria-label="Automations and schedules"
      value={tab}
      items={[
        { value: "automations", label: "Automations", href: "/automations" },
        { value: "schedules", label: "Schedules", href: "/schedules" },
      ]}
    />
  );
}

/** For admins, the Routines page and New routine, at the top of both tabs: routines run on Lens's clock too. On
 * Automations, New automation is the primary button, so New routine isn't. */
export function RoutineLinks({ primary = true }: { primary?: boolean }) {
  const { admin } = useArchive();
  if (!admin) return null;
  return (
    <>
      <Button asChild size="sm">
        <Link href="/routines">Routines</Link>
      </Button>
      <Button asChild size="sm" variant={primary ? "primary" : undefined}>
        <Link href="/routines/new">
          <Plus /> New routine
        </Link>
      </Button>
    </>
  );
}
