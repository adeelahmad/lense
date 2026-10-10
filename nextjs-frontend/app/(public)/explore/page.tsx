import type { Metadata } from "next";

import { PublicHomeView } from "@/components/public/explore-views";

export const metadata: Metadata = {
  title: "Explore",
  description: "Browse the assets their owners have made public.",
};

/** The home page for visitors (docs/access.md). */
export default function ExploreRoute() {
  return <PublicHomeView />;
}
