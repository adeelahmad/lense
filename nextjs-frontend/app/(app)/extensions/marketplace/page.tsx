import type { Metadata } from "next";

import { MarketplacePage } from "@/components/extensions/marketplace-page";

export const metadata: Metadata = { title: "Marketplace" };

/** Skills and plugins from marketplaces in Anthropic's format, links and files. */
export default function Page() {
  return <MarketplacePage />;
}
