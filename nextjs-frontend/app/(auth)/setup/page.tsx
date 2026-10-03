import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { SetupForm } from "@/components/auth/setup-form";
import { isSetupRequired } from "@/lib/auth/status";

export const metadata: Metadata = { title: "Set up this server" };
export const dynamic = "force-dynamic";

/** The installer opens /setup?code=… so the setup code is already filled in. */
export default async function SetupPage({ searchParams }: { searchParams: Promise<{ code?: string | string[] }> }) {
  if (!(await isSetupRequired())) redirect("/login");
  const { code } = await searchParams;
  return <SetupForm initialCode={typeof code === "string" ? code : ""} />;
}
