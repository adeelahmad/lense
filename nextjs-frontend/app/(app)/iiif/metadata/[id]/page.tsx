import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { MetadataEditor } from "@/components/iiif/metadata-editor";

export const metadata: Metadata = { title: "Metadata" };

/** A recording's descriptive metadata, full page (MD1). The Recording page's Metadata tab shows the same editor. */
export default async function RecordingMetadataPage({ params }: { params: Promise<{ id: string }> }) {
  const id = Number((await params).id);
  if (!Number.isInteger(id) || id <= 0) notFound();
  return (
    <div className="px-4 py-5 sm:px-6">
      <MetadataEditor recordingId={id} variant="page" />
    </div>
  );
}
