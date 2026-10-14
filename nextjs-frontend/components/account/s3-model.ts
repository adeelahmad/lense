import type { S3KeyCreated } from "@/app/openapi-client/types.gen";

/** The rclone remote for a key: what people paste into rclone.conf (or adapt for any S3 client). */
export function rcloneConfig(host: string, created: Pick<S3KeyCreated, "id" | "secret" | "port">): string {
  return [
    "[lens]",
    "type = s3",
    "provider = Other",
    `access_key_id = ${created.id}`,
    `secret_access_key = ${created.secret}`,
    `endpoint = http://${host}:${created.port}`,
    "force_path_style = true",
  ].join("\n");
}

/** How to mount the WebDAV share with a key: its address, then the key id and secret as user name and password. */
export function webdavText(host: string, created: Pick<S3KeyCreated, "id" | "secret" | "webdav_port">): string {
  return [
    `address  = http://${host}:${created.webdav_port}/`,
    `user     = ${created.id}`,
    `password = ${created.secret}`,
  ].join("\n");
}
