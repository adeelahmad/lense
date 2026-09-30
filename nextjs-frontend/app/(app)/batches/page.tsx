import type { Metadata } from "next";

import { BatchList } from "@/components/batches/batch-list";

export const metadata: Metadata = { title: "Batch runs" };

export default function Page() {
  return <BatchList />;
}
