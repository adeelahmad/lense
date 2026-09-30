import { redirect } from "next/navigation";

import { auth } from "@/auth";
import { createApiClient } from "@/lib/api/client";

/**
 * The API client for the signed-in user, for server components and server
 * actions. Sends the session's access token; redirects to /login when there is
 * no usable session.
 *
 *   const client = await getApiClient();
 *   const { data } = await Auth.me({ client });
 */
export async function getApiClient() {
  const session = await auth();
  if (!session?.accessToken || session.error) redirect("/login");
  return createApiClient(session.accessToken);
}
