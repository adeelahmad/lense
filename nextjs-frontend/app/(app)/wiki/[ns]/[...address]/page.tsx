import type { Metadata } from "next";

import { WikiPage } from "@/components/wiki/entity-page";
import { addressOf, titleOf } from "@/components/wiki/links";

type Params = { params: Promise<{ ns: string; address: string[] }> };

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const { address } = await params;
  return { title: titleOf(addressOf(address)) };
}

/** An entity's page, by name: /wiki/<namespace>/<Name_As_Written> (app/domain/wiki.py). */
export default async function Page({ params }: Params) {
  const { ns, address } = await params;
  return <WikiPage ns={addressOf([ns])} address={addressOf(address)} />;
}
