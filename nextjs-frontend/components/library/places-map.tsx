"use client";

import "leaflet/dist/leaflet.css";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import type * as Leaflet from "leaflet";
import { MapPinOff } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { Resources } from "@/app/openapi-client";
import type { RecordingSummary } from "@/app/openapi-client/types.gen";
import { boundsOf, mapCount, mapQuery, placed, placeText, type Placed } from "@/components/library/map-model";
import type { LibraryQuery } from "@/components/library/model";
import { Button } from "@/components/ui/button";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { data, page, useApiClient } from "@/lib/api/browser";
import { shortDate } from "@/lib/format";

/** Text for Leaflet's attribution, which it reads as HTML: the admin's words, shown as words. */
function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}

/** The tile server's host, which sees the area looked at (threat-model.md). */
function tileHost(url: string): string {
  try {
    return new URL(url.replace(/\{[a-z]+\}/g, "a")).host;
  } catch {
    return url;
  }
}

/** A dot's popup: the asset's title (a link to it), its date and where. Built as nodes, so a title stays text. */
function popup(r: Placed, open: (r: Placed) => void): HTMLElement {
  const box = document.createElement("div");
  const a = document.createElement("a");
  a.href = `/resources/${r.id}`;
  a.textContent = r.title || "Untitled";
  a.style.fontWeight = "700"; // Leaflet's popups are light in either theme, with colours of their own
  a.addEventListener("click", (e) => {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
    e.preventDefault();
    open(r);
  });
  const sub = document.createElement("div");
  sub.style.color = "#555";
  sub.textContent = [r.recorded_at ? shortDate(r.recorded_at) : null, r.namespace, placeText(r.place)]
    .filter(Boolean)
    .join(" · ");
  box.append(a, sub);
  return box;
}

/**
 * The Library as a map: each asset that says where it was taken (a photo's EXIF GPS fix, a video's location) as a dot,
 * with the Library's filters, framed to show them all. The tiles under it come from Settings → Maps; the browser
 * fetches them, so the tile server sees the area looked at, which the line under the map says.
 */
export function PlacesMap({ query, onOpen }: { query: LibraryQuery; onOpen?: (r: RecordingSummary) => void }) {
  const client = useApiClient();
  const router = useRouter();
  const tiles = useQuery({
    queryKey: ["map-tiles"],
    queryFn: () => data(Resources.getMapTiles({ client })),
    staleTime: 5 * 60_000,
  });
  const q = useMemo(() => mapQuery(query), [query]);
  const found = useQuery({
    queryKey: ["recordings", "map", q],
    queryFn: () => page(Resources.listRecordings({ client, query: q })),
    placeholderData: keepPreviousData,
  });
  const points = useMemo(() => placed(found.data?.items ?? []), [found.data]);
  const el = useRef<HTMLDivElement>(null);
  const lib = useRef<{ L: typeof Leaflet; map: Leaflet.Map; dots: Leaflet.LayerGroup } | null>(null);
  const [ready, setReady] = useState(false);
  // a popup's link opens the asset in the app (the popups are made once per dot, so they call the latest of this)
  const openRef = useRef<(r: Placed) => void>(() => {});
  useEffect(() => {
    openRef.current = (r: Placed) => {
      onOpen?.(r);
      router.push(`/resources/${r.id}`);
    };
  }, [onOpen, router]);

  const url = tiles.data?.url ?? null;
  const attribution = tiles.data?.attribution ?? null;
  // Leaflet reads `window` as it loads, so it's loaded here, in the browser, once the tiles are known
  useEffect(() => {
    if (!tiles.isSuccess || !el.current) return;
    let gone = false;
    let made: Leaflet.Map | null = null;
    void import("leaflet").then((mod) => {
      const L = (mod as { default?: typeof Leaflet }).default ?? (mod as typeof Leaflet);
      if (gone || !el.current) return;
      made = L.map(el.current, { worldCopyJump: true }).setView([20, 0], 2);
      if (url)
        L.tileLayer(url, { maxZoom: 19, attribution: attribution ? escapeHtml(attribution) : undefined }).addTo(made);
      lib.current = { L, map: made, dots: L.layerGroup().addTo(made) };
      setReady(true);
    });
    return () => {
      gone = true;
      made?.remove();
      lib.current = null;
      setReady(false);
    };
  }, [tiles.isSuccess, url, attribution]);

  // The dots, and the view framed around them, each time the filters bring others
  useEffect(() => {
    const m = lib.current;
    if (!ready || !m) return;
    m.dots.clearLayers();
    for (const r of points) {
      m.L.circleMarker([r.place.lat, r.place.lon], {
        radius: 7,
        weight: 2,
        fillOpacity: 0.9,
        className: "fill-blue stroke-background",
      })
        .bindTooltip(r.title || "Untitled")
        .bindPopup(() => popup(r, (x) => openRef.current(x)))
        .addTo(m.dots);
    }
    const b = boundsOf(points.map((r) => r.place));
    if (b) m.map.fitBounds(b, { padding: [32, 32], maxZoom: 15 });
  }, [ready, points]);

  if (found.isError || tiles.isError) {
    return (
      <EmptyState
        tone="error"
        className="border-t border-border"
        title="Couldn’t load the map"
        actions={
          <Button
            variant="secondary"
            onClick={() => {
              void tiles.refetch();
              void found.refetch();
            }}
          >
            Try again
          </Button>
        }
      >
        {((found.error ?? tiles.error) as Error).message}
      </EmptyState>
    );
  }
  const total = found.data?.total ?? 0;
  return (
    <div className="flex flex-col gap-2 border-t border-border px-4 pt-3 md:px-6">
      <div className="relative isolate z-0 h-[62vh] min-h-[320px] overflow-hidden rounded-[10px] border border-border bg-surface-neutral">
        {!tiles.isSuccess && <Skeleton className="absolute inset-0 h-full w-full" />}
        <div ref={el} className="size-full" aria-label="Map of where assets were taken" role="region" />
        {found.isSuccess && !total && (
          <div className="pointer-events-none absolute inset-x-0 top-6 z-[1000] flex justify-center">
            <div className="pointer-events-auto flex max-w-sm items-start gap-2 rounded-[10px] border border-border bg-background px-3 py-2 text-[13px] leading-snug text-fg-secondary shadow-2">
              <MapPinOff className="mt-px size-4 shrink-0" aria-hidden />
              <span>
                None of these assets say where they were taken. Photos with a GPS fix in their EXIF, and videos with a
                location, show up here as they arrive.
              </span>
            </div>
          </div>
        )}
      </div>
      <p className="tabular text-[12.5px] text-fg-muted">
        {found.isSuccess ? mapCount(points.length, total) : "Loading…"}
        {" · "}
        {url ? `Map tiles from ${tileHost(url)}, which sees the area you look at` : "No map tiles: Settings → Maps"}
      </p>
      <ul className="sr-only" aria-label="Placed assets">
        {points.map((r) => (
          <li key={r.id}>
            <a href={`/resources/${r.id}`} onClick={() => onOpen?.(r)}>
              {r.title || "Untitled"}, {placeText(r.place)}
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}
