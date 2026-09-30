/** Short names for storage types, as the design writes them ("Dropbox · /Apps/CallRecorder", "SFTP · calls-gw"). */
export const SOURCE_TYPE_LABEL: Record<string, string> = {
  s3: "S3",
  dropbox: "Dropbox",
  drive: "Google Drive",
  onedrive: "OneDrive",
  sftp: "SFTP",
  smb: "SMB",
  webdav: "WebDAV",
  local: "Local",
};

export function sourceTypeLabel(type: string | null | undefined, fallback = "Source"): string {
  return (type && SOURCE_TYPE_LABEL[type]) || fallback;
}
