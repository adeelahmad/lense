import type { Metadata } from "next";

import { FeedsScreen } from "@/components/feeds/feeds-screen";

export const metadata: Metadata = { title: "Feeds" };

/** Published RSS and podcast feeds (backend: app/domain/syndication.py). */
export default function FeedsPage() {
  return <FeedsScreen />;
}
