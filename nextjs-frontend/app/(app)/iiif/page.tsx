import type { Metadata } from "next";
import { Suspense } from "react";

import { CollectionsIndex } from "@/components/iiif/collections";

export const metadata: Metadata = { title: "Collections" };

/** IIIF Collections (IP3), and the landing point for shared moments (?iiif-content=…, IP2). */
export default function IiifPage() {
  return (
    <Suspense>
      <CollectionsIndex />
    </Suspense>
  );
}
