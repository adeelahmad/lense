/** Where a link in a note goes in the app: "recording:12", "entity:5", "topic:2", "page:3", "collection:4", "speaker:7". */
export function hrefFor(target: string, ns?: string | null): string | null {
  const [kind, id] = target.split(":");
  if (!id || !/^\d+$/.test(id)) return null;
  const q = ns ? `&ns=${encodeURIComponent(ns)}` : "";
  switch (kind) {
    case "page":
      return `/notes/${id}`;
    case "recording":
      return `/resources/${id}`;
    case "entity":
      return `/entities?entity=${id}${q}`;
    case "topic":
      return `/topics/${id}`;
    case "speaker":
      return `/speakers/${id}`;
    case "collection":
      return ns ? `/library?namespace=${encodeURIComponent(ns)}&collection=${id}` : "/library";
    default:
      return null;
  }
}

/** The page of a thing: /notes/about/entity/5. */
export function pageOf(target: string): string {
  const [kind, id] = target.split(":");
  return `/notes/about/${kind}/${id}`;
}

export const PLACES = [
  { value: "project", label: "Project" },
  { value: "area", label: "Area" },
  { value: "resource", label: "Resource" },
  { value: "archive", label: "Archive" },
] as const;
