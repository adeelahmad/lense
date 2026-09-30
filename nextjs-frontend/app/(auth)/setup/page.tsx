import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { SetupForm } from "@/components/auth/setup-form";
import { isSetupRequired } from "@/lib/auth/status";

export const metadata: Metadata = { title: "Set up" };
export const dynamic = "force-dynamic";

export default async function SetupPage() {
  if (!(await isSetupRequired())) redirect("/login");
  return <SetupForm />;
}
