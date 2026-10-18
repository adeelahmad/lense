import type { Metadata } from "next";

import { AutomationsPage } from "@/components/automations/automations-page";

export const metadata: Metadata = { title: "Automations" };

/** Automations: when something happens, if a test passes, do something. */
export default function Page() {
  return <AutomationsPage />;
}
