import { redirect } from "next/navigation";

/** Settings open on their first section. */
export default function SettingsIndex() {
  redirect("/settings/transcription");
}
