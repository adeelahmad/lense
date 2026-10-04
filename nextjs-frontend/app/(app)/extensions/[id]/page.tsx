import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { ExtensionEditor } from "@/components/extensions/extension-editor";

export const metadata: Metadata = { title: "Extension" };

/** One extension: its manifest, who can use it, trying it, and its versions. */
export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const n = Number(id);
  if (!Number.isInteger(n) || n < 1) notFound();
  return <ExtensionEditor id={n} />;
}
