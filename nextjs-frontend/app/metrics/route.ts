import { handlers } from "@/lib/api/backend-proxy";

// The Prometheus scrape endpoint (docs/prometheus.md), answered by the API: off by default, and only for a scraper
// that sends the token set in Settings › Telemetry. Served here so it follows LENS_BIND like the web app.
export const dynamic = "force-dynamic";
export const { GET } = handlers;
