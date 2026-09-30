import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { CollectionDetail } from "@/components/collections/collection-detail";

export const metadata: Metadata = { title: "Collection" };

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!/^\d+$/.test(id)) notFound();
  return <CollectionDetail id={Number(id)} />;
}
