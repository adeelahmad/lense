import { handlers } from "@/lib/api/backend-proxy";

// ActivityPub for circles with other servers (docs/federation.md): actors, outboxes and signed inboxes, answered by
// the API; see lib/api/backend-proxy.ts.
export const dynamic = "force-dynamic";
export const { GET, HEAD, POST, OPTIONS } = handlers;
