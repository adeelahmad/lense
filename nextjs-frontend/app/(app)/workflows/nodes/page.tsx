import type { Metadata } from "next";

import { CustomNodesPage } from "@/components/workflows/custom-nodes-page";

export const metadata: Metadata = { title: "Custom nodes" };

/** Custom nodes: bodies of nodes saved under a name, used in workflows and shared. */
export default function Page() {
  return <CustomNodesPage />;
}
