import type { Metadata } from "next";

import { LibraryScreen } from "@/components/library/library-screen";

export const metadata: Metadata = { title: "Library" };

type Props = {
  searchParams: Promise<{ namespace?: string | string[]; collection?: string | string[]; nsfw?: string | string[] }>;
};

const SENSITIVE = ["include", "only", "exclude"] as const;

const one = (v?: string | string[]) => (Array.isArray(v) ? v[0] : v);

/** Library (L1–L6): every recording you can see, with live progress; ?namespace=…&collection=… opens a collection, and
 * &nsfw=only (include, exclude) filters by NSFW classification. */
export default async function LibraryPage({ searchParams }: Props) {
  const p = await searchParams;
  const namespace = one(p.namespace);
  const collection = Number(one(p.collection));
  const nsfw = SENSITIVE.find((s) => s === one(p.nsfw));
  return (
    <LibraryScreen
      initial={
        namespace
          ? {
              namespace,
              collection: Number.isInteger(collection) && collection > 0 ? collection : null,
              sensitive: nsfw,
            }
          : undefined
      }
    />
  );
}
