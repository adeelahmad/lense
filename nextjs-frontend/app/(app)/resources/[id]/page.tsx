import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { parseFileFocus } from "@/components/recording/files-model";
import { parseStart } from "@/components/recording/model";
import { RecordingPage } from "@/components/recording/recording-page";

// The recording's own title replaces this once it loads (client-side, so the page never waits on the API to render).
export const metadata: Metadata = { title: "Recording" };

type Props = {
  params: Promise<{ id: string }>;
  searchParams: Promise<{
    t?: string | string[];
    file?: string | string[];
    line?: string | string[];
    page?: string | string[];
  }>;
};

/** A resource: a recording (R1–R9, VR1–VR3), a document or an image. `?t=<seconds>` opens it at that moment,
 * `?page=<n>` a document on its nth page, `?file=<id>&line=<n>` on one of its files (search links lines of files
 * without times that way). /recordings/<id> redirects here. */
export default async function RecordingRoute({ params, searchParams }: Props) {
  const { id } = await params;
  const rid = Number(id);
  if (!Number.isInteger(rid) || rid <= 0) notFound();
  const { t, file, line, page } = await searchParams;
  return (
    <RecordingPage
      id={rid}
      start={parseStart(t)}
      focus={parseFileFocus(file, line)}
      page={typeof page === "string" ? page : null}
    />
  );
}
