import type { Metadata } from "next";

import { RolesPage } from "@/components/admin/roles";

export const metadata: Metadata = { title: "Roles" };

/** Roles and their permissions: the built-in ones and the custom ones admins make. */
export default function AdminRolesPage() {
  return <RolesPage />;
}
