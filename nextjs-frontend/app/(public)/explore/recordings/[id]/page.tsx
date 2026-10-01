import type { Metadata } from "next";
import { headers } from "next/headers";
import { notFound } from "next/navigation";

import { Public } from "@/app/openapi-client";
import { auth } from "@/auth";
import { first, type Meta } from "@/components/iiif/metadata-model";
import { parsePageParam } from "@/components/public/model";
import { PublicRecordingView } from "@/components/public/recording-view";
import { parseStart } from "@/components/recording/model";
import { createApiClient } from "@/lib/api/client";

type Props = {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ t?: string | string[]; page?: string | string[] }>;
};

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
  const client = createApiClient(token, (await headers()).get("x-forwarded-for"));
  const { data } = await Public.getPublicRecording({ client, path: { rid } }).catch(() => ({ data: undefined }));
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

/** A recording's public page (docs/access.md). `?t=<seconds>` opens it at that moment; a document's `?page=<n>` there. */
export default async function PublicRecordingRoute({ params, searchParams }: Props) {
  const { t, page } = await searchParams;
  return <PublicRecordingView id={recordingId((await params).id)} start={parseStart(t)} page={parsePageParam(page)} />;
}
