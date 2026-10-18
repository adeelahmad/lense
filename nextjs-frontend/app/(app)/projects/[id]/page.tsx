import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { ProjectScreen } from "@/components/projects/project-screen";

export const metadata: Metadata = { title: "Project" };

/** A project: its goal, its coordinator, and its threads with what each is doing. */
export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const pid = Number(id);
  if (!Number.isInteger(pid) || pid <= 0) notFound();
  return <ProjectScreen key={pid} id={pid} />;
}
