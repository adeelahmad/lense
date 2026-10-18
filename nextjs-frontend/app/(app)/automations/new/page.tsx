import type { Metadata } from "next";

import { NewAutomationPage } from "@/components/automations/automation-page";

export const metadata: Metadata = { title: "New automation" };

/** A new automation, from the builder or a sentence. */
export default function Page() {
  return <NewAutomationPage />;
}
