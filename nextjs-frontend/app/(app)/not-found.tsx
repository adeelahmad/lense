import type { Metadata } from "next";

import { NotFoundState } from "@/components/errors/error-states";

export const metadata: Metadata = { title: "Not found" };

/** Unknown pages inside the app, shown within the shell (Access AC6). */
export default function AppNotFound() {
  return <NotFoundState className="min-h-[calc(100vh-64px)]" />;
}
