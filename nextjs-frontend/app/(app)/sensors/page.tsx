import type { Metadata } from "next";

import { SensorsPage } from "@/components/sensors/sensors-page";

export const metadata: Metadata = { title: "Sensors" };

/** Sensors: everything that feeds Lens, the hub's inbox of new devices, and the file sources. */
export default function Page() {
  return <SensorsPage />;
}
