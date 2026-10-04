import type { Metadata } from "next";

import { HomePage as Home } from "@/components/home/home-page";

export const metadata: Metadata = { title: "Home" };

/** Home: assistant mode once the archive has content (one field, one mic), else the overview (HM1). */
export default function HomePage() {
  return <Home />;
}
