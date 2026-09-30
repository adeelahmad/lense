import type { Metadata } from "next";

import { SourcesPage } from "@/components/sources/sources-page";

export const metadata: Metadata = { title: "Sources" };

/** Sources: storage connections and watched folders (SO1–SO3). */
export default function Page() {
  return <SourcesPage />;
}
