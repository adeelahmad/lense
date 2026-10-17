import { redirect } from "next/navigation";

/** A namespace's wiki: its entities, each a link to its page. */
export default async function Page({ params }: { params: Promise<{ ns: string }> }) {
  const { ns } = await params;
  redirect(`/entities?ns=${ns}`);
}
