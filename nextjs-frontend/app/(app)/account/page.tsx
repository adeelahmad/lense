import type { Metadata } from "next";

import { ProfilePage } from "@/components/account/profile";

export const metadata: Metadata = { title: "Profile and sign-in" };

export default function AccountPage() {
  return <ProfilePage />;
}
