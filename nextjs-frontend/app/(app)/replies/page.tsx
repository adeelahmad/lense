import type { Metadata } from "next";
import { Suspense } from "react";

import { RepliesPage } from "@/components/replies/replies-page";

export const metadata: Metadata = { title: "Replies" };

/** Email that may need an answer, each with its cited draft (backend: GET /api/v1/replies, docs/email-replies.md). */
export default function Page() {
  return (
    <Suspense>
      <RepliesPage />
    </Suspense>
  );
}
