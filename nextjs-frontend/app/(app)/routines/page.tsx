import type { Metadata } from "next";

import { RoutinesPage } from "@/components/routines/routines-page";

export const metadata: Metadata = { title: "Routines" };

/** Routines: syncs, pipelines and workflows run on a schedule (admins). */
export default function Page() {
  return <RoutinesPage />;
}
