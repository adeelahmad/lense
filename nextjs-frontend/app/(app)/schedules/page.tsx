import type { Metadata } from "next";

import { SchedulesPage } from "@/components/schedules/schedules-page";

export const metadata: Metadata = { title: "Schedules" };

/** Everything that runs on a clock, like a crontab: routines, automations' schedules and the backup (backend:
 * GET /api/v1/schedules, docs/schedules.md). */
export default function Page() {
  return <SchedulesPage />;
}
