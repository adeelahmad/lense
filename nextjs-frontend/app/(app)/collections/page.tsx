import type { Metadata } from "next";

import { CollectionsPage } from "@/components/collections/collections-page";

export const metadata: Metadata = { title: "Collections" };

/** Saved collections (CR1). */
export default function Page() {
  return <CollectionsPage />;
}
