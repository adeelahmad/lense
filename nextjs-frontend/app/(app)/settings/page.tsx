import { redirect } from "next/navigation";

/** Settings open on Speakers, the one section every member has. */
export default function SettingsIndex() {
  redirect("/settings/speakers");
}
