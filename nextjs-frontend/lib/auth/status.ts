import { Auth, type ExternalProvider } from "@/app/openapi-client";
import { createApiClient } from "@/lib/api/client";

async function status() {
  try {
    const { data } = await Auth.status({ client: createApiClient() });
    return data;
  } catch {
    return undefined;
  }
}

/** Whether the archive still needs its first admin. False when the API can't be reached. */
export async function isSetupRequired(): Promise<boolean> {
  return (await status())?.setup_required ?? false;
}

/** Whether a fresh install's setup wizard is still to be finished (admins are taken to /welcome). False when the API
 * can't be reached. */
export async function isWizardPending(): Promise<boolean> {
  return (await status())?.wizard_pending ?? false;
}

/** Whether passwords sign in here (auth.passwords); passkeys always do. False when the API can't be reached. */
export async function passwordsOn(): Promise<boolean> {
  return (await status())?.passwords ?? false;
}

/** The outside accounts people can sign in with (Settings › Sign-in). Empty when the API can't be reached. */
export async function externalProviders(): Promise<ExternalProvider[]> {
  try {
    const { data } = await Auth.externalProviders({ client: createApiClient() });
    return data ?? [];
  } catch {
    return [];
  }
}
