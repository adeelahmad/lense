import type { Metadata } from "next";

import { HealthPage } from "@/components/admin/health";

export const metadata: Metadata = { title: "System health" };

/** Database, workers, sources, disk and queue at a glance (Admin AD5). */
export default function AdminHealthPage() {
  return <HealthPage />;
}
