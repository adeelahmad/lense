import type { Metadata } from "next";

import { ProjectsScreen } from "@/components/projects/projects-screen";

export const metadata: Metadata = { title: "Projects" };

/** Goals worked toward in threads, each with a coordinator (backend: app/domain/projects.py). */
export default function ProjectsPage() {
  return <ProjectsScreen />;
}
