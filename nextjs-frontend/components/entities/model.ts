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
};

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
