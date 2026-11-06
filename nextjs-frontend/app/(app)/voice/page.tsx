import type { Metadata } from "next";

import { VoiceWorkspace } from "@/components/voice/voice-workspace";

export const metadata: Metadata = { title: "Voice" };

export default function VoicePage() {
  return <VoiceWorkspace />;
}
