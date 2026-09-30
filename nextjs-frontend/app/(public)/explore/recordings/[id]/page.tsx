import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { Public } from "@/app/openapi-client";
import { auth } from "@/auth";
import { first, type Meta } from "@/components/iiif/metadata-model";
import { PublicRecordingView } from "@/components/public/recording-view";
import { createApiClient } from "@/lib/api/client";

type Props = { params: Promise<{ id: string }> };

function recordingId(id: string): number {
  const rid = Number(id);
  if (!Number.isInteger(rid) || rid <= 0) notFound();
  return rid;
}

/** The title and summary for the browser tab and for link previews, as this visitor may see them. */
export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const rid = recordingId((await params).id);
  const session = await auth();
  const token = session && !session.error ? session.accessToken : undefined;
  const { data } = await Public.getPublicRecording({ client: createApiClient(token), path: { rid } }).catch(() => ({
    data: undefined,
  }));
  if (!data || data.view === "locked") return { title: data?.title ?? "Recording", robots: { index: false } };
  const summary = first((data.description as Meta | null)?.summary) || undefined;
  return {
    title: data.title ?? "Recording",
    description: summary,
    openGraph: { title: data.title ?? "Recording", description: summary, type: "website" },
    // only public recordings belong in search engines
    robots: data.access === "public" ? undefined : { index: false },
  };
}

/** A recording's public page (docs/access.md). */
export default async function PublicRecordingRoute({ params }: Props) {
  return <PublicRecordingView id={recordingId((await params).id)} />;
}
