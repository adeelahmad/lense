import type { Metadata } from "next";

import { SigninLinkForm } from "@/components/auth/signin-link-form";

export const metadata: Metadata = { title: "Add a passkey" };

/** A one-time sign-in link (an admin's, `lens users link`, or "Lost your passkey?"): the token is in the address's
 * fragment, so it never reaches a server log. */
export default function SigninLinkPage() {
  return <SigninLinkForm />;
}
