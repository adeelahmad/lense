import { redirect } from "next/navigation";

/** Speakers moved into Settings; old links (and bookmarks with ?ns= or ?tab=) land there. */
export default async function Page({ searchParams }: { searchParams: Promise<Record<string, string | string[]>> }) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(await searchParams)) for (const x of [v].flat()) p.append(k, x);
  const q = p.toString();
  redirect(`/settings/speakers${q ? `?${q}` : ""}`);
}
