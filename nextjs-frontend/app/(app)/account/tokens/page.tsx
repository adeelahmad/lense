import type { Metadata } from "next";

import { TokensPage } from "@/components/account/tokens";

export const metadata: Metadata = { title: "API tokens" };

/** API tokens: list · create · shown once · revoke (Access AC5). */
export default function AccountTokensPage() {
  return <TokensPage />;
}
