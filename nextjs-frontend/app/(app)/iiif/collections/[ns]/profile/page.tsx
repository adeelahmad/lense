import type { Metadata } from "next";

import { ProfileEditor } from "@/components/iiif/profile-editor";

export async function generateMetadata({ params }: { params: Promise<{ ns: string }> }): Promise<Metadata> {
  return { title: `${decodeURIComponent((await params).ns)} · Metadata profile` };
}

/** A namespace's metadata profile: required fields, defaults, vocabularies, default access (MD3). */
export default async function IiifProfilePage({ params }: { params: Promise<{ ns: string }> }) {
  const { ns } = await params;
  return <ProfileEditor ns={decodeURIComponent(ns)} />;
}
