import type { Metadata } from "next";

import { AuditPage } from "@/components/admin/audit";

export const metadata: Metadata = { title: "Audit log" };

/** Who changed what (Admin AD4). */
export default function AdminAuditPage() {
  return <AuditPage />;
}
