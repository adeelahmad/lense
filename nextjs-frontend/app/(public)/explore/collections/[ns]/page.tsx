import type { Metadata } from "next";

import { Public } from "@/app/openapi-client";
import { auth } from "@/auth";
import { PublicCollectionView } from "@/components/public/explore-views";
import { createApiClient } from "@/lib/api/client";

type Props = { params: Promise<{ ns: string }> };

/** The title and summary for the browser tab and link previews; only collections visitors can see are indexable. */
export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const name = decodeURIComponent((await params).ns);
  const get = (token?: string) =>
    Public.getPublicCollection({ client: createApiClient(token), path: { name }, query: { limit: 1 } })
      .then((r) => r.data)
      .catch(() => undefined);
  const open = await get();
  const session = open ? null : await auth();
  const c = open ?? (session && !session.error ? await get(session.accessToken) : undefined);
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
