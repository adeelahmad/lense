import { notFound, redirect } from "next/navigation";

/** Entities have no page of their own in the design: an entity opens in the graph with its panel. */
export default async function EntityPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!/^\d+$/.test(id)) notFound();
  redirect(`/graph?focus=e${id}`);
}
