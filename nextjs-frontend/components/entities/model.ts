/** The Entities page: list rows (the API returns them untyped), the query it sends and how the URL keeps its filters. */

export type EntityRow = {
  id: number;
  name: string;
  type: string;
  type_label: string;
  key: string;
  description?: string | null;
  namespace?: string | null;
  aliases: string[];
  mentions: number;
  recordings: number;
  first?: string | null;
  last?: string | null;
  hidden: boolean;
  /** On the fixed list people defined. */
  defined?: boolean;
  /** "unknown" or "unlabeled": one of the two entities that are always there. */
  builtin?: string | null;
  /** A defined entity of one collection (and those inside it). */
  collection?: number | null;
};

/** "a, b , ,c" → ["a", "b", "c"]: the other ways an entity is said, typed as a list. */
export function splitAliases(text: string): string[] {
  return [
    ...new Set(
      text
        .split(/[,\n]/)
        .map((x) => x.trim())
        .filter(Boolean),
    ),
  ];
}

export type EntityFilters = {
  ns: string;
  q: string;
  type: string;
  collection: number | null;
  hidden: boolean;
  sort: "mentions" | "recordings" | "recent" | "name";
  offset: number;
};

export const PAGE = 50;
export const SORTS = ["mentions", "recordings", "recent", "name"] as const;

/** The /entities list query for these filters. */
export function entityQuery(f: EntityFilters) {
  return {
    namespaces: f.ns,
    q: f.q.trim() || undefined,
    types: f.type || undefined,
    collection: f.collection ?? undefined,
    hidden: f.hidden,
    sort: f.sort,
    limit: PAGE,
    offset: f.offset,
  };
}

/** Filters from the address (?q=&type=&collection=&view=hidden&sort=&page=), defaults for anything missing or odd. */
export function filtersFrom(params: URLSearchParams, ns: string): EntityFilters {
  const col = Number(params.get("collection"));
  const sort = params.get("sort") ?? "";
  const page = Math.max(1, Math.floor(Number(params.get("page")) || 1));
  return {
    ns,
    q: params.get("q") ?? "",
    type: params.get("type") ?? "",
    collection: Number.isInteger(col) && col > 0 ? col : null,
    hidden: params.get("view") === "hidden",
    sort: (SORTS as readonly string[]).includes(sort) ? (sort as EntityFilters["sort"]) : "mentions",
    offset: (page - 1) * PAGE,
  };
}

/** "Northwind Labs, NWL": the name people search by, then what else it's been called. */
export function alsoKnownAs(e: Pick<EntityRow, "aliases" | "key" | "name">): string[] {
  const own = e.name.toLowerCase();
  return [...new Set(e.aliases.filter((a) => a && a !== own && a !== e.key))];
}

/** "Organisations and places" / "Every type": what a setup keeps, in words. */
export function keptTypes(types: string[], all: { type: string; label: string }[]): string {
  if (!types.length) return "Every type";
  const label = (t: string) => all.find((x) => x.type === t)?.label ?? t;
  const names = types.map(label);
  return names.length === 1 ? names[0] : `${names.slice(0, -1).join(", ")} and ${names.at(-1)}`;
}

export type Mode = "self" | "fixed" | "hybrid";

/** The ways a namespace or collection can organise its entities (entity_setup.py). */
export const MODES: { value: Mode; label: string; hint: string }[] = [
  {
    value: "self",
    label: "Self-organizing",
    hint: "Every name found becomes an entity; people merge, rename and describe them.",
  },
  {
    value: "fixed",
    label: "Fixed list",
    hint: "Editors define the entities. Names found go to one of them, to Unlabeled when they belong here but fit none, or to Unknown.",
  },
  {
    value: "hybrid",
    label: "A few fixed, then self-organizing",
    hint: "Names go to a defined entity first. Other names of the types that belong here become entities of their own; the rest go to Unknown.",
  },
];

/** What the types checkboxes mean in a mode. */
export function typesWording(mode: string): { legend: string; hint: string; all: string } {
  if (mode === "hybrid")
    return {
      legend: "Types that belong here",
      all: "Every type but dates and numbers",
      hint: "A name of one of these types that matches no defined entity becomes an entity of its own; any other name goes to Unknown.",
    };
  return mode === "fixed"
    ? {
        legend: "Types that belong here",
        all: "Every type but dates and numbers",
        hint: "A name of one of these types that matches no defined entity goes to Unlabeled; any other name goes to Unknown.",
      }
    : {
        legend: "Types to keep",
        all: "Every type",
        hint: "Names of other types are left out when a recording is analysed.",
      };
}

/** How names that aren't an entity's name or other name are placed. */
export const MATCHING: { value: "rules" | "model"; label: string; hint: string }[] = [
  {
    value: "rules",
    label: "By name only",
    hint: "A name goes to the entity it spells, in any case, or one of its other names.",
  },
  {
    value: "model",
    label: "By name, then by description (uses the LLM)",
    hint: "Names the rules can't place go to the LLM with the entities’ descriptions and other names. A name it places becomes one of that entity’s other names. Uses the LLM provider in Settings; without one, names are placed by name only.",
  },
];
