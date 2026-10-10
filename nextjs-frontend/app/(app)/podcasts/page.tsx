import type { Metadata } from "next";

import { PodcastsScreen } from "@/components/podcasts/podcasts-screen";

export const metadata: Metadata = { title: "Podcasts" };

/** Episodes made from picked resources (backend: app/domain/podcasts.py). */
export default function PodcastsPage() {
  return <PodcastsScreen />;
}
