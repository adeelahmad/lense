import { notFound, permanentRedirect } from "next/navigation";

type Props = {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

/** /recordings/<id>, the address recordings had before they were resources: it moved to /resources/<id>, with its
 * query (`?t=`) kept. */
export default async function OldRecordingRoute({ params, searchParams }: Props) {
  const { id } = await params;
  if (!/^\d+$/.test(id)) notFound();
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(await searchParams))
    for (const x of Array.isArray(v) ? v : v != null ? [v] : []) q.append(k, x);
  permanentRedirect(`/resources/${id}${q.size ? `?${q}` : ""}`);
}
