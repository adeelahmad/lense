import type { Metadata } from "next";

import { ContentTypesPage } from "@/components/content-types/content-types-page";

export const metadata: Metadata = { title: "Content types" };

/** Content types: the base types, the vocabulary of subtypes under them and their pipelines. */
export default function Page() {
  return <ContentTypesPage />;
}
