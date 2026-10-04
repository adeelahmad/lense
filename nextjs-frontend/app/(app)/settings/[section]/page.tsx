import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import { SECTIONS, WORKSPACE_SECTIONS, type AnySectionId } from "@/components/settings/model";
import { SettingsShell } from "@/components/settings/settings-shell";

const ALL = [...WORKSPACE_SECTIONS, ...SECTIONS];

export async function generateMetadata({ params }: { params: Promise<{ section: string }> }): Promise<Metadata> {
  const { section } = await params;
  const s = ALL.find((x) => x.id === section);
  return { title: s ? `${s.label} · Settings` : "Settings" };
}

/** One settings section: /settings/speakers (every member), /settings/voice-ids, /settings/iiif… (admins). */
export default async function SettingsPage({ params }: { params: Promise<{ section: string }> }) {
  const { section } = await params;
  if (!ALL.some((s) => s.id === section)) notFound();
  return (
    <Suspense>
      <SettingsShell section={section as AnySectionId} />
    </Suspense>
  );
}
