import type { Metadata } from "next";

import { ExternalSigninReturn } from "@/components/auth/external-signin-return";

export const metadata: Metadata = { title: "Signing in" };

/** Where signing in with an outside account comes back to. The one-time ticket is in the address's fragment, so it
 * never reaches a server log. */
export default function ExternalSigninPage() {
  return <ExternalSigninReturn />;
}
