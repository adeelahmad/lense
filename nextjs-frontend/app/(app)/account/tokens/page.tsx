import type { Metadata } from "next";

import { ConnectedApps } from "@/components/account/apps";
import { TokensPage } from "@/components/account/tokens";

export const metadata: Metadata = { title: "API tokens" };

/** API tokens: list · create · shown once · revoke (Access AC5); and the apps given access through OAuth. */
export default function AccountTokensPage() {
  return (
    <>
      <TokensPage />
      <ConnectedApps />
    </>
  );
}
