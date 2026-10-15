import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { ToEntityPage } from "@/components/wiki/to-entity-page";

export const metadata: Metadata = { title: "Entity" };

/** An entity by id (what links and the RDF URI /id/entity/<id> use): its own page, by name, under /wiki. */
export default async function EntityPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!/^\d+$/.test(id)) notFound();
  return <ToEntityPage id={Number(id)} />;
}
