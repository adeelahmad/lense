import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { SpeakerProfile } from "@/components/speakers/profile";

export const metadata: Metadata = { title: "Person" };

/** A person's profile (SP4). */
export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!/^\d+$/.test(id)) notFound();
  return <SpeakerProfile id={Number(id)} />;
}
