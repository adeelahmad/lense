import type { Metadata } from "next";

import { NamespacesPage } from "@/components/admin/namespaces";

export const metadata: Metadata = { title: "Namespaces" };

/** Namespaces (admins create them; owners manage their members). */
export default function AdminNamespacesPage() {
  return <NamespacesPage />;
}
