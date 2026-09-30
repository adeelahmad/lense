import type { Metadata } from "next";

import { NewTemplate } from "@/components/templates/new-template";

export const metadata: Metadata = { title: "New template" };

export default function Page() {
  return <NewTemplate />;
}
