import type { Metadata } from "next";

import { IiifImport } from "@/components/iiif/import";

export const metadata: Metadata = { title: "Import from IIIF" };

/** Import audio, captions and metadata from another IIIF archive (IP4). Admins only: it's a source. */
export default function IiifImportPage() {
  return <IiifImport />;
}
