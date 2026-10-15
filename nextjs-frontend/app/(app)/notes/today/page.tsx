import type { Metadata } from "next";

import { TodayNote } from "@/components/notes/today";

export const metadata: Metadata = { title: "Today’s note" };

/** Today's note: made the first time it's asked for, then opened. A bookmark or ⌘K gets there in one step. */
export default function Page() {
  return <TodayNote />;
}
