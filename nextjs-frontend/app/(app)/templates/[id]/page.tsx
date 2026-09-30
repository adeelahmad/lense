import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { TemplateEditor } from "@/components/templates/template-editor";

export const metadata: Metadata = { title: "Template" };

/** One template: editor, preview, output schema and versions (TP1, TP2). */
export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const n = Number(id);
  if (!Number.isInteger(n) || n < 1) notFound();
  return <TemplateEditor id={n} />;
}
