import type { Metadata } from "next";

import { HomeScreen } from "@/components/home/home-screen";

export const metadata: Metadata = { title: "Home" };

/** Home (HM1): what needs you, what just arrived, what's processing, and a quick way to import. */
export default function HomePage() {
  return <HomeScreen />;
}
