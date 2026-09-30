import type { Metadata } from "next";

import { PipelineEditor } from "@/components/pipelines/pipeline-editor";

export const metadata: Metadata = { title: "New pipeline" };

export default function Page() {
  return <PipelineEditor />;
}
