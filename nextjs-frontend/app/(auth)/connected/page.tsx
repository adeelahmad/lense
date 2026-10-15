import type { Metadata } from "next";

import { ConnectedReturn } from "@/components/sources/connected-return";

export const metadata: Metadata = { title: "Connected" };

/** Where signing a connection in at its service (Google Drive) comes back to, in the window that sign-in opened. */
export default function ConnectedPage() {
  return <ConnectedReturn />;
}
