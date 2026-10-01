"use client";

import { FieldValuesPanel } from "@/components/fields/fields-ui";
import { IiifPanel } from "@/components/iiif/iiif-panel";
import { MetadataEditor } from "@/components/iiif/metadata-editor";
import { useRec } from "@/components/recording/context";

/** IIIF tab: the recording's IIIF panel (manifest, what it carries, access, validation), built by the IIIF area. */
export function IiifTab() {
  const { id } = useRec();
  return <IiifPanel recordingId={id} />;
}

/** Metadata tab: the recording's custom fields, then its descriptive metadata editor (the one-column panel variant). */
export function MetadataTab() {
  const { id } = useRec();
  return (
    <>
      <FieldValuesPanel source={{ kind: "resource", rid: id }} className="border-b border-border pb-4" />
      <MetadataEditor recordingId={id} variant="panel" />
    </>
  );
}
