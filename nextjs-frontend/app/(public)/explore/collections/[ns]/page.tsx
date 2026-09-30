import type { Metadata } from "next";
import { headers } from "next/headers";

import { Public } from "@/app/openapi-client";
import { auth } from "@/auth";
import { PublicCollectionView } from "@/components/public/explore-views";
import { createApiClient } from "@/lib/api/client";

type Props = { params: Promise<{ ns: string }> };

/**
 * The title and summary for the browser tab and link previews. Only collections anyone can see are indexable; others
 * get their title for this visitor (signed in, or from an IP group's addresses).
 */
export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const name = decodeURIComponent((await params).ns);
  const get = (token?: string, forwardedFor?: string | null) =>
    Public.getPublicCollection({ client: createApiClient(token, forwardedFor), path: { name }, query: { limit: 1 } })
      .then((r) => r.data)
      .catch(() => undefined);
  const open = await get();
  const session = open ? null : await auth();
  const token = session && !session.error ? session.accessToken : undefined;
  const forwardedFor = open ? null : (await headers()).get("x-forwarded-for");
  const c = open ?? (token || forwardedFor ? await get(token, forwardedFor) : undefined);
  if (!c) return { title: "Collection", robots: { index: false } };
  return {
    title: c.label,
    description: c.summary ?? undefined,
    openGraph: { title: c.label, description: c.summary ?? undefined, type: "website" },
    robots: open ? undefined : { index: false },
  };
}

/** A collection's page for visitors (docs/access.md). */
export default async function PublicCollectionRoute({ params }: Props) {
  return <PublicCollectionView name={decodeURIComponent((await params).ns)} />;
}
