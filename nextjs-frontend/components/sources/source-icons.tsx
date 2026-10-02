import {
  Box,
  CalendarDays,
  Cloud,
  CloudCog,
  Folder,
  Globe,
  HardDrive,
  type LucideIcon,
  Mail,
  Network,
  Server,
} from "lucide-react";

import type { SourceType } from "@/components/sources/source-model";

export const TYPE_ICON: Record<SourceType, LucideIcon> = {
  s3: Cloud,
  dropbox: Box,
  drive: HardDrive,
  onedrive: CloudCog,
  sftp: Server,
  smb: Network,
  webdav: Globe,
  local: Folder,
  imap: Mail,
  ical: CalendarDays,
};

/** The connection's type in a 32px rounded tile. */
export function TypeTile({ type, size = 32 }: { type: SourceType; size?: number }) {
  const Icon = TYPE_ICON[type] ?? Cloud;
  return (
    <span
      aria-hidden
      className="grid shrink-0 place-items-center rounded-[9px] bg-surface-neutral text-fg-secondary"
      style={{ width: size, height: size }}
    >
      <Icon style={{ width: size * 0.53, height: size * 0.53 }} />
    </span>
  );
}
