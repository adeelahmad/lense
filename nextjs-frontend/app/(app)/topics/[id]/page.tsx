import { notFound, redirect } from "next/navigation";

/** A topic opens in the Topics page, in its own namespace (the address RDF sends browsers to). */
export default async function TopicPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!/^\d+$/.test(id)) notFound();
  redirect(`/topics?topic=${id}`);
}
