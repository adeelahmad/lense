import type { Metadata } from "next";

import { LibraryScreen } from "@/components/library/library-screen";

export const metadata: Metadata = { title: "Library" };

/** Library (L1–L6): every recording you can see, with live progress. */
export default function LibraryPage() {
  return <LibraryScreen />;
}
