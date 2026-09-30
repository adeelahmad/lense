import type { Metadata } from "next";
import { Suspense } from "react";

import { SpeakersPage } from "@/components/speakers/speakers-page";

export const metadata: Metadata = { title: "Speakers" };

/** Speakers (SP1–SP3). */
export default function Page() {
  return (
    <Suspense>
      <SpeakersPage />
    </Suspense>
  );
}
