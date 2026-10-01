import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { FieldValuesPanel } from "@/components/fields/fields-ui";
import { MetadataEditor } from "@/components/iiif/metadata-editor";

export const metadata: Metadata = { title: "Metadata" };

/** A recording's custom fields and descriptive metadata, full page (MD1). The Recording page's Metadata tab shows the
 * same. */
export default async function RecordingMetadataPage({ params }: { params: Promise<{ id: string }> }) {
  const id = Number((await params).id);
  if (!Number.isInteger(id) || id <= 0) notFound();
  return (
    <div className="flex flex-col gap-5 px-4 py-5 sm:px-6">
      <FieldValuesPanel source={{ kind: "resource", rid: id }} className="max-w-[720px]" />
      <MetadataEditor recordingId={id} variant="page" />
    </div>
  );
}
