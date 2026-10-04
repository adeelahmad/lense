import type { Metadata } from "next";

import { HubLoginsPage } from "@/components/sensors/hub-logins";

export const metadata: Metadata = { title: "Hub logins" };

/** The usernames devices sign in to the MQTT hub with (admins). */
export default function Page() {
  return <HubLoginsPage />;
}
