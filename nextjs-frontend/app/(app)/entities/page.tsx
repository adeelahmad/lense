import type { Metadata } from "next";
import { Suspense } from "react";

import { EntitiesPage } from "@/components/entities/entities-page";

export const metadata: Metadata = { title: "Entities" };

/** The people, organisations, products, places and topics a namespace's recordings mention. */
export default function Page() {
  return (
    <Suspense>
      <EntitiesPage />
    </Suspense>
  );
}
