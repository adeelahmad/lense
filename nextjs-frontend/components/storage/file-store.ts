/** Where Lens keeps its own files (Settings → Storage, the backend's `files` section: app/domain/blobs.py). */

/** Connections files can't be kept on: email accounts and calendars are read, not written (feeds.py). */
export const NOT_STORAGE = new Set(["imap", "ical"]);

export type FileStoreTry =
  { store: "local"; crypt: false } | { store: "connection"; connection: number | null; folder: string; crypt: boolean };

/** What to check, or save: the choices as they stand. */
export function fileStoreTry(store: unknown, connection: unknown, folder: unknown, crypt: unknown): FileStoreTry {
  if (store !== "connection") return { store: "local", crypt: false };
  return {
    store: "connection",
    connection: connection === "" || connection == null ? null : Number(connection),
    folder: String(folder ?? "").trim(),
    crypt: Boolean(crypt),
  };
}
