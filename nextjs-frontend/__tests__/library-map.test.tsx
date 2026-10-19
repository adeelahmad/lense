import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";
import type { ReactNode } from "react";

import { Resources } from "@/app/openapi-client";
import type { RecordingSummary } from "@/app/openapi-client/types.gen";
import { boundsOf, mapCount, mapQuery, placed, placeText } from "@/components/library/map-model";
import { PlacesMap } from "@/components/library/places-map";
import { crossErrors } from "@/components/settings/model";

jest.mock("@/app/openapi-client", () => ({
  Resources: { getMapTiles: jest.fn(), listRecordings: jest.fn() },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
const push = jest.fn();
jest.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

// Leaflet draws into the page with SVG and canvas jsdom lacks: what the map asks of it is recorded instead
const calls: { tiles: unknown[][]; dots: unknown[][]; fit: unknown[][] } = { tiles: [], dots: [], fit: [] };
jest.mock("leaflet", () => {
  const layer = () => {
    const l = {
      addTo: () => l,
      bindTooltip: () => l,
      bindPopup: () => l,
      clearLayers: () => l,
    };
    return l;
  };
  const map = () => {
    const m = {
      setView: () => m,
      fitBounds: (...a: unknown[]) => calls.fit.push(a),
      remove: () => undefined,
    };
    return m;
  };
  return {
    map,
    layerGroup: layer,
    tileLayer: (...a: unknown[]) => (calls.tiles.push(a), layer()),
    circleMarker: (...a: unknown[]) => (calls.dots.push(a), layer()),
  };
});

const ok = (data: unknown, total?: number) =>
  Promise.resolve({
    data,
    response: { ok: true, status: 200, headers: new Headers(total != null ? { "x-total-count": String(total) } : {}) },
  });
const m = (f: unknown) => f as jest.Mock;
const row = (id: number, title: string, place: { lat: number; lon: number } | null) =>
  ({
    id,
    title,
    place,
    space: 1,
    namespace: "pods",
    media_kind: "image",
    recorded_at: "2019-08-17T09:41:05",
  }) as RecordingSummary;
const EIFFEL = row(1, "Eiffel Tower", { lat: 48.8582, lon: 2.2945 });
const OPERA = row(2, "Sydney Opera House", { lat: -33.8568, lon: 151.2153 });

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  calls.tiles = [];
  calls.dots = [];
  calls.fit = [];
});

test("the map's model: placed rows, the box around them, the query and the words", () => {
  expect(placed([EIFFEL, row(3, "Nowhere", null), OPERA]).map((r) => r.id)).toEqual([1, 2]);
  expect(boundsOf([EIFFEL.place!, OPERA.place!])).toEqual([
    [-33.8568, 2.2945],
    [48.8582, 151.2153],
  ]);
  expect(boundsOf([])).toBeNull();
  // the Library's filters, only assets with a place, as many as the map draws
  expect(mapQuery({ ns: "pods", q: "tower", sort: "-date" })).toEqual({
    ns: "pods",
    q: "tower",
    sort: "-date",
    located: true,
    limit: 1000,
    offset: 0,
  });
  expect(placeText(OPERA.place!)).toBe("33.8568° S, 151.2153° E");
  expect(mapCount(2, 2)).toBe("2 placed assets");
  expect(mapCount(1000, 1500)).toBe("The newest 1,000 of 1,500 placed assets · narrow the filters to see the rest");
  expect(mapCount(0, 0)).toMatch(/No assets/);
});

test("placed assets are dots on the tiles from Settings, framed to show them all", async () => {
  m(Resources.getMapTiles).mockReturnValue(
    ok({ url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png", attribution: "© OpenStreetMap <contributors>" }),
  );
  m(Resources.listRecordings).mockReturnValue(ok([EIFFEL, OPERA], 2));
  wrap(<PlacesMap query={{ ns: "pods", sort: "-date" }} />);
  await waitFor(() => expect(calls.dots).toHaveLength(2));
  expect(m(Resources.listRecordings).mock.calls[0][0].query).toMatchObject({ ns: "pods", located: true, limit: 1000 });
  // the tile server's address, with the credit as text (Leaflet reads it as HTML)
  expect(calls.tiles[0][0]).toBe("https://tile.openstreetmap.org/{z}/{x}/{y}.png");
  expect(calls.tiles[0][1]).toMatchObject({ attribution: "© OpenStreetMap &#60;contributors&#62;" });
  expect(calls.dots.map((d) => d[0])).toEqual([
    [48.8582, 2.2945],
    [-33.8568, 151.2153],
  ]);
  expect(calls.fit[0][0]).toEqual([
    [-33.8568, 2.2945],
    [48.8582, 151.2153],
  ]);
  expect(screen.getByText(/2 placed assets/)).toHaveTextContent(
    "Map tiles from tile.openstreetmap.org, which sees the area you look at",
  );
  // each one, for a screen reader, as a link to it
  expect(screen.getByRole("link", { name: /Eiffel Tower, 48.8582° N/ })).toHaveAttribute("href", "/resources/1");
});

test("with no tiles set only the dots are drawn, and nothing placed says why", async () => {
  m(Resources.getMapTiles).mockReturnValue(ok({ url: null, attribution: null }));
  m(Resources.listRecordings).mockReturnValue(ok([], 0));
  wrap(<PlacesMap query={{ sort: "-date" }} />);
  expect(await screen.findByText(/None of these assets say where they were taken/)).toBeInTheDocument();
  expect(screen.getByText(/No map tiles: Settings → Maps/)).toBeInTheDocument();
  await waitFor(() => expect(calls.fit).toHaveLength(0));
  expect(calls.tiles).toHaveLength(0);
});

test("Settings → Maps takes a tile address with {z}, {x} and {y}, or none", () => {
  expect(crossErrors({ "maps.tile_url": "https://tiles.home.lan/{z}/{x}/{y}.png" })["maps.tile_url"]).toBeUndefined();
  expect(crossErrors({ "maps.tile_url": null })["maps.tile_url"]).toBeUndefined();
  expect(crossErrors({ "maps.tile_url": "https://tiles.home.lan/tile.png" })["maps.tile_url"]).toMatch(/\{z\}/);
  expect(crossErrors({ "maps.tile_url": "tiles.home.lan/{z}/{x}/{y}" })["maps.tile_url"]).toMatch(/http/);
});
