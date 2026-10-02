import type { Metadata } from "next";

import { AnalyticsScreen } from "@/components/analytics/analytics-screen";

export const metadata: Metadata = { title: "Analytics" };

/** How a namespace's or a collection's resources are used (docs/analytics.md). */
export default function AnalyticsPage() {
  return <AnalyticsScreen />;
}
