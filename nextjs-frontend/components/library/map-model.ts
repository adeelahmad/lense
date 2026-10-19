/**
 * The Library's Map: which assets go on it (those whose file said where they were taken, `place`), the box that frames
 * them, and the query that asks for them with the Library's own filters.
 */
import type { GeoPoint, RecordingSummary } from "@/app/openapi-client/types.gen";
import type { LibraryQuery } from "@/components/library/model";

/** Assets drawn on the map at most: the newest this many that match. */
export const MAP_LIMIT = 1000;

export type Placed = RecordingSummary & { place: GeoPoint };

/** The list endpoint's query for the map: the Library's filters, sort and namespace, only assets with a place. */
export function mapQuery(q: LibraryQuery): LibraryQuery {
  return { ...q, located: true, limit: MAP_LIMIT, offset: 0 };
}

/** The rows that have a place, in the order given. */
export function placed(rows: readonly RecordingSummary[]): Placed[] {
  return rows.filter(
    (r): r is Placed => r.place != null && Number.isFinite(r.place.lat) && Number.isFinite(r.place.lon),
  );
}

/** The south-west and north-east corners ([lat, lon]) of the box around these points; null for none. */
export function boundsOf(points: readonly GeoPoint[]): [[number, number], [number, number]] | null {
  if (!points.length) return null;
  const lats = points.map((p) => p.lat);
  const lons = points.map((p) => p.lon);
  return [
    [Math.min(...lats), Math.min(...lons)],
    [Math.max(...lats), Math.max(...lons)],
  ];
}

/** "48.8582° N, 2.2945° E": a place as people read it, to four places (about 10 m). */
export function placeText(p: GeoPoint): string {
  const f = (v: number, pos: string, neg: string) => `${Math.abs(v).toFixed(4)}° ${v < 0 ? neg : pos}`;
  return `${f(p.lat, "N", "S")}, ${f(p.lon, "E", "W")}`;
}

/** What the count line under the map says: how many are drawn, and how many more match when the cap cut them. */
export function mapCount(shown: number, total: number): string {
  const n = (x: number) => x.toLocaleString("en");
  if (!total) return "No assets here say where they were taken";
  if (total > shown) return `The newest ${n(shown)} of ${n(total)} placed assets · narrow the filters to see the rest`;
  return total === 1 ? "1 placed asset" : `${n(total)} placed assets`;
}
