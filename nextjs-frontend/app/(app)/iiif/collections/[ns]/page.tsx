import type { Metadata } from "next";

import { CollectionPage } from "@/components/iiif/collection-page";

export async function generateMetadata({ params }: { params: Promise<{ ns: string }> }): Promise<Metadata> {
  return { title: `${decodeURIComponent((await params).ns)} · Collection` };
}

/** One namespace as a IIIF Collection (IP3). */
export default async function IiifCollectionPage({ params }: { params: Promise<{ ns: string }> }) {
  const { ns } = await params;
  return <CollectionPage ns={decodeURIComponent(ns)} />;
}
