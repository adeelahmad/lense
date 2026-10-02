import type { Metadata } from "next";

import { ActivityPage } from "@/components/account/activity";

export const metadata: Metadata = { title: "Your activity" };

export default function AccountActivityPage() {
  return <ActivityPage />;
}
