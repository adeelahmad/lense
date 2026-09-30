import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { SECTIONS, type SectionId } from "@/components/settings/model";
import { SettingsShell } from "@/components/settings/settings-shell";

export async function generateMetadata({ params }: { params: Promise<{ section: string }> }): Promise<Metadata> {
  const { section } = await params;
  const s = SECTIONS.find((x) => x.id === section);
  return { title: s ? `${s.label} · Settings` : "Settings" };
}

/** One settings section (admins): /settings/voice-ids, /settings/iiif… */
export default async function SettingsPage({ params }: { params: Promise<{ section: string }> }) {
  const { section } = await params;
  if (!SECTIONS.some((s) => s.id === section)) notFound();
  return <SettingsShell section={section as SectionId} />;
}
