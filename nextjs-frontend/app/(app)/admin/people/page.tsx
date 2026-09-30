import type { Metadata } from "next";

import { PeoplePage } from "@/components/admin/people";

export const metadata: Metadata = { title: "People" };

/** Accounts and the role matrix (Admin AD1–AD2). */
export default function AdminPeoplePage() {
  return <PeoplePage />;
}
