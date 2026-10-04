import type { Metadata } from "next";

import { NotePage } from "@/components/notes/note-page";

export const metadata: Metadata = { title: "Note" };

/** A free note, opened from the tree in the left navigation or from a link in another note. */
export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <NotePage key={id} id={Number(id)} />;
}
