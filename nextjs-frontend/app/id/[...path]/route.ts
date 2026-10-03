import { handlers } from "@/lib/api/backend-proxy";

// Proxied to the FastAPI backend at request time; see lib/api/backend-proxy.ts.
export const dynamic = "force-dynamic";
export const { GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS } = handlers;
