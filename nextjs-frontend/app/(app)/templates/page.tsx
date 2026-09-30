import type { Metadata } from "next";

import { TemplatesPage } from "@/components/templates/templates-page";

export const metadata: Metadata = { title: "Templates" };

/** Templates list (the Templates tab of Pipelines). */
export default function Page() {
  return <TemplatesPage />;
}
