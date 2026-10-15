import type { Metadata } from "next";

import { NotePage } from "@/components/notes/note-page";
import { ToEntityPage } from "@/components/wiki/to-entity-page";

export const metadata: Metadata = { title: "Note" };

/** The page of a recording, collection, topic or speaker: a draft until someone writes on it. An entity's notes are
 * on its own page, under /wiki. */
export default async function Page({ params }: { params: Promise<{ kind: string; key: string }> }) {
  const { kind, key } = await params;
  if (kind === "entity" && /^\d+$/.test(key)) return <ToEntityPage id={Number(key)} />;
  return <NotePage key={`${kind}:${key}`} about={`${kind}:${key}`} />;
}
