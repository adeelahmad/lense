import type { Metadata } from "next";

import { NotesHome } from "@/components/notes/notes-home";

export const metadata: Metadata = { title: "Notes" };

/** Notes: free notes of the namespace in view, newest first; the tree is in the left navigation. */
export default function Page() {
  return <NotesHome />;
}
