"use client";

/**
 * Secret fields that refer to a password manager (docs/secrets.md): what a saved reference shows (its reference and
 * whether the server could read it, never a value), and the picker that fills a field with a reference to one of the
 * vault's items. Settings provides whether a vault is set up (SecretsProvider); outside it, there's no picker.
 */
import { useQuery } from "@tanstack/react-query";
import { KeySquare } from "lucide-react";
import { createContext, useContext, useState, type ReactNode } from "react";

import { Admin } from "@/app/openapi-client";
import type { SecretProvider, VaultItem } from "@/app/openapi-client/types.gen";
import { Button } from "@/components/ui/button";
import { SearchInput } from "@/components/ui/field";
import { data, useApiClient } from "@/lib/api/browser";
import { relative } from "@/lib/format";
import { cn } from "@/lib/utils";

/** A secret field's saved value as Settings shows it: set, and for a reference, the reference and its read status. */
export type SavedSecret = {
  set?: boolean;
  ref?: string;
  provider?: string;
  read?: boolean | null;
  error?: string | null;
  read_at?: string | null;
  missing?: boolean;
};

const SCHEMES = ["vaultwarden"];

/** Whether text is a reference to a password manager's item, like vaultwarden://OpenAI/password. */
export function isReference(v: unknown): v is string {
  if (typeof v !== "string") return false;
  const m = /^\s*([a-z][a-z0-9+.-]*):\/\//i.exec(v);
  return Boolean(m && SCHEMES.includes(m[1].toLowerCase()));
}

export function savedReference(saved: unknown): SavedSecret | null {
  const s = saved as SavedSecret | null | undefined;
  return s && typeof s === "object" && typeof s.ref === "string" ? s : null;
}

const LABELS: Record<string, string> = { vaultwarden: "Vaultwarden" };

/** "Vaultwarden" for vaultwarden://… */
export function providerLabel(ref: string): string {
  const scheme = ref.split("://")[0].toLowerCase();
  return LABELS[scheme] ?? scheme;
}

/** The status line under a saved reference: when it was read, or why not. */
export function ReferenceStatus({ saved }: { saved: SavedSecret }) {
  if (saved.read)
    return (
      <span className="text-green-dark">
        Read from {providerLabel(saved.ref ?? "")}
        {saved.read_at ? ` ${relative(saved.read_at)}` : ""}.
      </span>
    );
  if (saved.read === false)
    return <span className="text-red-dark">Not read: {saved.error || "the vault didn’t answer"}.</span>;
  return <span>Not read on this server yet.</span>;
}

type Vaults = { providers: SecretProvider[] };
const SecretsContext = createContext<Vaults | null>(null);

/** Whether a password manager is set up here, for the secret fields inside (only admins see Settings). */
export function SecretsProvider({ enabled, children }: { enabled: boolean; children: ReactNode }) {
  const client = useApiClient();
  const q = useQuery({
    queryKey: ["secrets"],
    queryFn: () => data(Admin.getSecrets({ client })),
    enabled,
    staleTime: 60_000,
  });
  return (
    <SecretsContext.Provider value={q.data ? { providers: q.data.providers } : null}>
      {children}
    </SecretsContext.Provider>
  );
}

/** The password managers set up, which a secret field can refer to. */
export function useVaults(): SecretProvider[] {
  const v = useContext(SecretsContext);
  return (v?.providers ?? []).filter((p) => p.scheme && p.configured);
}

/** The vault's items (names and field names, never values): pick a field, and its reference fills the secret. */
export function VaultPicker({ provider, onPick }: { provider: SecretProvider; onPick: (ref: string) => void }) {
  const client = useApiClient();
  const [q, setQ] = useState("");
  const items = useQuery({
    queryKey: ["secrets", "items", provider.id, q],
    queryFn: () =>
      data(Admin.listVaultItems({ client, query: { provider: provider.id, q: q || undefined, limit: 50 } })),
    staleTime: 30_000,
  });
  const list: VaultItem[] = items.data?.items ?? [];
  const first = list[0];
  const firstRef = first ? (first.ref ?? Object.values(first.refs ?? {})[0]) : undefined;
  return (
    <div className="flex flex-col gap-2 rounded-sm border border-border bg-surface p-2.5">
      <SearchInput
        autoFocus
        aria-label={`Find an item in ${provider.label}`}
        placeholder={`Find an item in ${provider.label}`}
        value={q}
        onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && firstRef) {
            e.preventDefault();
            onPick(firstRef);
          }
        }}
      />
      {items.isPending ? (
        <p className="text-[12.5px] text-fg-muted">Reading the vault…</p>
      ) : items.isError ? (
        <p role="alert" className="text-[12.5px] text-red-dark">
          {items.error.message}
        </p>
      ) : list.length === 0 ? (
        <p className="text-[12.5px] text-fg-muted">
          {q ? `No item in ${provider.label}’s folder is called that.` : `${provider.label}’s folder has no items yet.`}
        </p>
      ) : (
        <ul className="flex max-h-64 flex-col gap-1 overflow-y-auto">
          {list.map((it) => (
            <li
              key={it.name}
              className="flex flex-wrap items-center gap-1.5 rounded-sm px-1.5 py-1 hover:bg-background"
            >
              <span className="min-w-0 flex-1 truncate text-[13px] font-semibold">{it.name}</span>
              <span className="text-[11.5px] text-fg-muted">{it.type}</span>
              {Object.entries(it.refs ?? {}).map(([field, ref]) => (
                <Button
                  key={field}
                  size="xs"
                  variant={ref === it.ref || (!it.ref && ref === firstRef) ? "secondary" : "ghost"}
                  onClick={() => onPick(ref)}
                >
                  {field}
                </Button>
              ))}
            </li>
          ))}
        </ul>
      )}
      <p className="text-[12px] text-fg-muted">Only the reference is saved. Lens reads the value from the vault.</p>
    </div>
  );
}

/** The button beside a secret field that opens the picker, when a vault is set up. */
export function VaultButton({ open, onClick }: { open: boolean; onClick: () => void }) {
  const vaults = useVaults();
  if (!vaults.length) return null;
  return (
    <Button
      variant="ghost"
      size="xs"
      icon={<KeySquare />}
      aria-expanded={open}
      onClick={onClick}
      className={cn(open && "bg-surface-neutral")}
    >
      From {vaults[0].label}
    </Button>
  );
}
