/**
 * Finding a setting by name: the settings sections in their groups, and a search over the sections' names and
 * descriptions and their fields' labels, hints and choices, for the Find box in Settings and ⌘K.
 */
import {
  FIELDS,
  fieldId,
  SECTIONS,
  WORKSPACE_SECTIONS,
  type AnySectionId,
  type FieldSpec,
  type SectionId,
} from "@/components/settings/model";

/** The archive's settings sections (admins), in groups, most used first in each. Every section is in one group. */
export const SETTINGS_GROUPS: { label: string; sections: SectionId[] }[] = [
  { label: "AI and search", sections: ["llm", "local-model", "ai", "sandbox", "search", "analysis"] },
  { label: "Speech and voices", sections: ["transcription", "speaker-separation", "voice-ids", "speech-providers"] },
  {
    label: "Media and documents",
    sections: ["uploads", "documents", "video", "nsfw", "enhance", "reports", "maps", "iiif"],
  },
  { label: "Chat and email", sections: ["telegram", "slack", "bridge", "mailbox", "mail", "notifications"] },
  {
    label: "Security and access",
    sections: ["sign-in", "tokens", "encryption", "secrets", "access", "remote-access", "https", "federation"],
  },
  { label: "Files and devices", sections: ["storage", "shares", "smb", "nfs", "fedora", "sensors"] },
  { label: "Nodes and network", sections: ["nodes", "network"] },
  {
    label: "System",
    sections: [
      "updates",
      "backups",
      "components",
      "workers",
      "scaling",
      "automations",
      "logs",
      "telemetry",
      "analytics",
      "startup",
    ],
  },
];

export type SettingHit = {
  section: AnySectionId;
  /** The section's name. */
  label: string;
  /** Its group in Settings ("Your namespaces" for Speakers). */
  group: string;
  /** The fields in it that matched. */
  fields: FieldSpec[];
  /** Found by a field only, not by the section's name or description: it opens on the first field. */
  byField: boolean;
};

/** Where a setting opens: its section, with the field to scroll to and focus. */
export function settingHref(section: AnySectionId, field?: FieldSpec): string {
  return field ? `/settings/${section}?field=${encodeURIComponent(fieldId(field))}` : `/settings/${section}`;
}

const fold = (s: string) => s.toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, "").replace(/[’‘]/g, "'");

/** Sections (and the fields in them) holding every word of `term`, best first: a section by its name, then by its
 * group or description, then sections with a field whose label, hint, key or choices match. Admins search every
 * section; members only theirs (Speakers). */
export function findSettings(term: string, admin: boolean): SettingHit[] {
  const words = fold(term).split(/\s+/).filter(Boolean);
  if (!words.length) return [];
  const has = (text: string) => {
    const t = fold(text);
    return words.every((w) => t.includes(w));
  };
  const byName: SettingHit[] = [];
  const byAbout: SettingHit[] = [];
  const byField: SettingHit[] = [];
  for (const s of WORKSPACE_SECTIONS)
    if (has(`${s.label} ${s.id}`))
      byName.push({ section: s.id, label: s.label, group: "Your namespaces", fields: [], byField: false });
  if (!admin) return byName;
  for (const g of SETTINGS_GROUPS)
    for (const id of g.sections) {
      const s = SECTIONS.find((x) => x.id === id)!;
      const fields = FIELDS.filter(
        (f) =>
          s.backend.includes(f.section) &&
          has(
            `${f.label} ${f.hint ?? ""} ${f.unit ?? ""} ${fieldId(f)} ${(f.options ?? []).map((o) => o.label).join(" ")}`,
          ),
      );
      const hit = { section: s.id, label: s.label, group: g.label, fields, byField: false };
      if (has(`${s.label} ${s.id}`)) byName.push(hit);
      else if (has(`${g.label} ${s.description}`)) byAbout.push(hit);
      else if (fields.length) byField.push({ ...hit, byField: true });
    }
  return [...byName, ...byAbout, ...byField];
}

/** Where a hit opens: on its first field when only a field matched, else at the top of the section. */
export const hitHref = (h: SettingHit) => settingHref(h.section, h.byField ? h.fields[0] : undefined);
