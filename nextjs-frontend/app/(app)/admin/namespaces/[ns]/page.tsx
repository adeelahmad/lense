import type { Metadata } from "next";

import { NamespaceDetail } from "@/components/admin/namespaces";

export async function generateMetadata({ params }: { params: Promise<{ ns: string }> }): Promise<Metadata> {
  const { ns } = await params;
  return { title: `${decodeURIComponent(ns)} · Members` };
}

/** A namespace's members (Admin AD3) and settings. */
export default async function AdminNamespacePage({ params }: { params: Promise<{ ns: string }> }) {
  const { ns } = await params;
  return <NamespaceDetail ns={decodeURIComponent(ns)} />;
}
