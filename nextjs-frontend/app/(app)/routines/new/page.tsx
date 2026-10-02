import type { Metadata } from "next";

import { NewRoutine } from "@/components/routines/routine-detail";

export const metadata: Metadata = { title: "New routine" };

export default function Page() {
  return <NewRoutine />;
}
