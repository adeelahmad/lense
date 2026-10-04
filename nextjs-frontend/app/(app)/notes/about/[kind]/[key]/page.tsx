import type { Metadata } from "next";

import { NotePage } from "@/components/notes/note-page";

export const metadata: Metadata = { title: "Note" };

/** The page of a recording, entity, collection or speaker: a draft until someone writes on it. */
export default async function Page({ params }: { params: Promise<{ kind: string; key: string }> }) {
  const { kind, key } = await params;
  return <NotePage key={`${kind}:${key}`} about={`${kind}:${key}`} />;
}
