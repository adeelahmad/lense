import type { Metadata } from "next";

import { ExtensionEditor } from "@/components/extensions/extension-editor";

export const metadata: Metadata = { title: "New extension" };

/** A new extension, written as a manifest. */
export default function Page() {
  return <ExtensionEditor />;
}
