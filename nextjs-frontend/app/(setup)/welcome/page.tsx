import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { auth } from "@/auth";
import { SetupWizard } from "@/components/setup/setup-wizard";
import { isWizardPending } from "@/lib/auth/status";

export const metadata: Metadata = { title: "Set up Lens" };
export const dynamic = "force-dynamic";

/** The first-run setup wizard (admins of a fresh install): namespace, model provider, storage. */
export default async function WelcomePage() {
  const session = await auth();
  if (!session?.user?.admin || !(await isWizardPending())) redirect("/");
  return <SetupWizard />;
}
