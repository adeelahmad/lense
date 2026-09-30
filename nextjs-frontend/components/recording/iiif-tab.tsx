"use client";

import { IiifPanel } from "@/components/iiif/iiif-panel";
import { MetadataEditor } from "@/components/iiif/metadata-editor";
import { useRec } from "@/components/recording/context";

/** IIIF tab: the recording's IIIF panel (manifest, what it carries, access, validation), built by the IIIF area. */
export function IiifTab() {
  const { id } = useRec();
  return <IiifPanel recordingId={id} />;
}

/** Metadata tab: the recording's descriptive metadata editor (the one-column panel variant). */
export function MetadataTab() {
  const { id } = useRec();
  return <MetadataEditor recordingId={id} variant="panel" />;
}
