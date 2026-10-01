import type { Metadata } from "next";

import { LibraryScreen } from "@/components/library/library-screen";

export const metadata: Metadata = { title: "Library" };

type Props = { searchParams: Promise<{ namespace?: string | string[]; collection?: string | string[] }> };

const one = (v?: string | string[]) => (Array.isArray(v) ? v[0] : v);

/** Library (L1–L6): every recording you can see, with live progress; ?namespace=…&collection=… opens a collection. */
export default async function LibraryPage({ searchParams }: Props) {
  const p = await searchParams;
  const namespace = one(p.namespace);
  const collection = Number(one(p.collection));
  return (
    <LibraryScreen
      initial={
        namespace
          ? { namespace, collection: Number.isInteger(collection) && collection > 0 ? collection : null }
          : undefined
      }
    />
  );
}
