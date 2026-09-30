import { Auth } from "@/app/openapi-client";
import { createApiClient } from "@/lib/api/client";

/** Whether the archive still needs its first admin. False when the API can't be reached. */
export async function isSetupRequired(): Promise<boolean> {
  try {
    const { data } = await Auth.status({ client: createApiClient() });
    return data?.setup_required ?? false;
  } catch {
    return false;
  }
}
