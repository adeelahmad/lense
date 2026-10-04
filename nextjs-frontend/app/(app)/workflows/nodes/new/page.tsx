import type { Metadata } from "next";

import { CustomNodeEditor } from "@/components/workflows/custom-node-editor";

export const metadata: Metadata = { title: "New custom node" };

export default function Page() {
  return <CustomNodeEditor />;
}
