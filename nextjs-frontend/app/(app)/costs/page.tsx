import type { Metadata } from "next";

import { CostsPage } from "@/components/costs/costs-page";

export const metadata: Metadata = { title: "Costs" };

/** Costs: what the namespace picked (or all of Lens) spent, by day, model and resource, against its budget. */
export default function Page() {
  return <CostsPage />;
}
