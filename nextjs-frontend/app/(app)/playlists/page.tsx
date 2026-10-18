import type { Metadata } from "next";

import { PlaylistsPage } from "@/components/playlists/playlists-page";

export const metadata: Metadata = { title: "Playlists" };

/** Playlists: clips of moments, played back to back. */
export default function Page() {
  return <PlaylistsPage />;
}
