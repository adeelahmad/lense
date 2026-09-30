import type { Metadata } from "next";

import { PageHeader } from "@/components/ui/states";

export const metadata: Metadata = { title: "Home" };

/** Home (HM1) — built by the Home & Reports screen set. */
export default function HomePage() {
  return (
    <div className="p-6">
      <PageHeader title="Home" />
    </div>
  );
}
