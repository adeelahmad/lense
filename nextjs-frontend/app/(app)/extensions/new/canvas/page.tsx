import type { Metadata } from "next";

import { ToolGraphEditor } from "@/components/extensions/tool-graph-editor";

export const metadata: Metadata = { title: "New tool" };

/** A new assistant tool, drawn on the canvas. */
export default function Page() {
  return <ToolGraphEditor />;
}
