import type { Metadata } from "next";

import { AnalyticsPage } from "@/components/analytics/analytics-page";

export const metadata: Metadata = { title: "Analytics" };

/** Built-in usage analytics: active people, churn, retention, features and the daily events (docs/analytics.md). */
export default function Page() {
  return <AnalyticsPage />;
}
