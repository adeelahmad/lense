import type { S3KeyCreated } from "@/app/openapi-client/types.gen";

/**
 * Where S3 and WebDAV clients reach the file shares: at the web app's own address (/s3 and /dav, so through remote
 * access too) once an admin opened them there (shares.remote), else at the share servers' ports on this host.
 */
export function shareAddresses(
  page: { origin: string; hostname: string },
  created: Pick<S3KeyCreated, "port" | "webdav_port">,
  remote: boolean,
): { s3: string; webdav: string } {
  if (remote) return { s3: `${page.origin}/s3`, webdav: `${page.origin}/dav/` };
  return { s3: `http://${page.hostname}:${created.port}`, webdav: `http://${page.hostname}:${created.webdav_port}/` };
}

/** The rclone remote for a key: what people paste into rclone.conf (or adapt for any S3 client). */
export function rcloneConfig(endpoint: string, created: Pick<S3KeyCreated, "id" | "secret">): string {
  return [
    "[lens]",
    "type = s3",
    "provider = Other",
    `access_key_id = ${created.id}`,
    `secret_access_key = ${created.secret}`,
    `endpoint = ${endpoint}`,
    "force_path_style = true",
  ].join("\n");
}

/** How to mount the WebDAV share with a key: its address, then the key id and secret as user name and password. */
export function webdavText(address: string, created: Pick<S3KeyCreated, "id" | "secret">): string {
  return [`address  = ${address}`, `user     = ${created.id}`, `password = ${created.secret}`].join("\n");
}
