import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { PlaylistPage } from "@/components/playlists/playlist-page";

export const metadata: Metadata = { title: "Playlist" };

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!/^\d+$/.test(id)) notFound();
  return <PlaylistPage id={Number(id)} />;
}
