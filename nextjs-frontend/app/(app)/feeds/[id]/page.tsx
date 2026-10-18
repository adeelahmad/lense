import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { FeedPage } from "@/components/feeds/feed-page";

export const metadata: Metadata = { title: "Feed" };

/** A feed: its private address, settings and items. */
export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const fid = Number(id);
  if (!Number.isInteger(fid) || fid <= 0) notFound();
  return <FeedPage key={fid} id={fid} />;
}
