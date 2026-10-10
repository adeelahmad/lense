import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { EpisodePage } from "@/components/podcasts/episode-page";

export const metadata: Metadata = { title: "Podcast" };

/** An episode: its progress while it's made, then the player, the cited script, its sources and the fact-check log. */
export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const rid = Number(id);
  if (!Number.isInteger(rid) || rid <= 0) notFound();
  return <EpisodePage key={rid} id={rid} />;
}
