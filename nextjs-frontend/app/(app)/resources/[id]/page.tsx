import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { parseStart } from "@/components/recording/model";
import { RecordingPage } from "@/components/recording/recording-page";

// The recording's own title replaces this once it loads (client-side, so the page never waits on the API to render).
export const metadata: Metadata = { title: "Recording" };

type Props = {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ t?: string | string[] }>;
};

/** A resource: a recording (R1–R9, VR1–VR3). `?t=<seconds>` opens it at that moment. /recordings/<id> redirects
 * here. */
export default async function RecordingRoute({ params, searchParams }: Props) {
  const { id } = await params;
  const rid = Number(id);
  if (!Number.isInteger(rid) || rid <= 0) notFound();
  const { t } = await searchParams;
  return <RecordingPage id={rid} start={parseStart(t)} />;
}
