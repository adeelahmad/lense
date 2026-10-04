import type { Metadata } from "next";

import { ExtensionsPage } from "@/components/extensions/extensions-page";

export const metadata: Metadata = { title: "Extensions" };

/** Extensions: tools, skills, hooks and plugins added to the assistant. */
export default function Page() {
  return <ExtensionsPage />;
}
