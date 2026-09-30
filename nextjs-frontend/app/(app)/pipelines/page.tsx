import type { Metadata } from "next";

import { PipelinesPage } from "@/components/pipelines/pipelines-page";

export const metadata: Metadata = { title: "Pipelines" };

/** Pipelines (PL1) and namespace defaults. */
export default function Page() {
  return <PipelinesPage />;
}
