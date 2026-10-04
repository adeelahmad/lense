import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { SensorDetailPage } from "@/components/sensors/sensor-detail";

export const metadata: Metadata = { title: "Sensor" };

/** One sensor: its streams, its kinds of log line, and its settings. */
export default async function Page({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ tab?: string }>;
}) {
  const { id } = await params;
  const { tab } = await searchParams;
  const n = Number(id);
  if (!Number.isInteger(n) || n < 1) notFound();
  return <SensorDetailPage id={n} tab={tab === "settings" ? "settings" : tab === "log" ? "log" : "streams"} />;
}
