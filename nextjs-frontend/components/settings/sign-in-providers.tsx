"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import { Auth, type ExternalProviderAdmin, type ExternalProviderSave } from "@/app/openapi-client";
import { ProviderIcon } from "@/components/auth/external-sign-in";
import { Badge } from "@/components/ui/badge";
import { Banner } from "@/components/ui/banner";
import { Button, IconButton } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select, Switch } from "@/components/ui/field";
import { CodeBlock, Skeleton } from "@/components/ui/states";
import { data, useApiClient } from "@/lib/api/browser";

type Kind = NonNullable<ExternalProviderSave["kind"]>;

const KINDS: { value: Kind; label: string }[] = [
  { value: "google", label: "Google" },
  { value: "github", label: "GitHub" },
  { value: "microsoft", label: "Microsoft" },
  { value: "oidc", label: "OpenID Connect (Authentik, Keycloak, Okta…)" },
];

/** Where an admin registers Lens with each kind of provider, to get a client id and secret. */
const WHERE: Record<Kind, string> = {
  google: "Google Cloud console › APIs & Services › Credentials › Create OAuth client ID (Web application).",
  github: "GitHub › Settings › Developer settings › OAuth Apps › New OAuth App.",
  microsoft: "Microsoft Entra admin center › App registrations › New registration (Web platform).",
  oidc: "Your provider's admin pages: add an OAuth2/OpenID application (confidential client).",
};

/** The web app's address for the provider's redirect URI: the address you're on. */
function callbackUrl(path: string): string {
  return typeof window === "undefined" ? path : `${window.location.origin}${path}`;
}

/**
 * Settings › Sign-in: the outside accounts people can sign in with (Google, GitHub, Microsoft, OpenID Connect). Each
 * shows the redirect URI to register at the provider; the client secret is kept sealed and never shown again.
 */
export function SignInProviders() {
  const client = useApiClient();
  const qc = useQueryClient();
  const [editing, setEditing] = useState<ExternalProviderAdmin | "new" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const list = useQuery({ queryKey: ["login-providers"], queryFn: () => data(Auth.listLoginProviders({ client })) });
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["login-providers"] });
    void qc.invalidateQueries({ queryKey: ["external-providers"] });
  };
  const toggle = useMutation({
    mutationFn: (p: ExternalProviderAdmin) =>
      data(Auth.changeLoginProvider({ client, path: { key: p.key }, body: { enabled: !p.enabled } })),
    onSuccess: refresh,
    onError: (e) => setError(e.message),
  });

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="text-[13px] font-bold text-fg-strong">Sign in with another account</div>
          <p className="text-[12.5px] leading-snug text-fg-muted">
            Google, GitHub, Microsoft or your own OpenID Connect provider. People are matched to their Lens account by a
            confirmed email address, or connect one in their profile.
          </p>
        </div>
        <Button size="sm" icon={<Plus />} onClick={() => setEditing("new")}>
          Add
        </Button>
      </div>
      {error && <Banner tone="error">{error}</Banner>}
      {list.isLoading ? (
        <Skeleton className="h-12 w-full" />
      ) : list.data?.length ? (
        <ul className="flex flex-col divide-y divide-border rounded-sm border border-border">
          {list.data.map((p) => (
            <li key={p.key} className="flex flex-wrap items-center gap-3 px-3 py-2.5 text-[13.5px]">
              <span className="text-fg-muted [&_svg]:size-4" aria-hidden>
                <ProviderIcon kind={p.kind} />
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 font-medium text-fg">
                  {p.label}
                  {p.signup && <Badge tone="intent">sign-up {p.domains.length ? p.domains.join(", ") : "open"}</Badge>}
                </div>
                <div className="text-[12px] text-fg-muted">
                  {p.people === 1 ? "1 person connected" : `${p.people} people connected`}
                </div>
              </div>
              <Switch
                checked={p.enabled}
                onCheckedChange={() => toggle.mutate(p)}
                aria-label={`${p.label} sign-in on`}
              />
              <IconButton label="Change" onClick={() => setEditing(p)}>
                <Pencil />
              </IconButton>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-[12.5px] text-fg-muted">None yet.</p>
      )}
      {editing && (
        <ProviderDialog
          provider={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={refresh}
        />
      )}
    </div>
  );
}

function ProviderDialog({
  provider,
  onClose,
  onSaved,
}: {
  provider: ExternalProviderAdmin | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const client = useApiClient();
  const [kind, setKind] = useState<Kind>(provider?.kind ?? "google");
  const [label, setLabel] = useState(provider?.label ?? "");
  const [clientId, setClientId] = useState(provider?.client_id ?? "");
  const [secret, setSecret] = useState("");
  const [issuer, setIssuer] = useState(provider?.issuer ?? "");
  const [tenant, setTenant] = useState(provider?.tenant ?? "");
  const [signup, setSignup] = useState(provider?.signup ?? false);
  const [domains, setDomains] = useState((provider?.domains ?? []).join(", "));
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [saved, setSaved] = useState<ExternalProviderAdmin | null>(null);

  const body: ExternalProviderSave = {
    label: label.trim() || null,
    client_id: clientId.trim(),
    ...(secret.trim() ? { client_secret: secret.trim() } : {}),
    ...(kind === "oidc" ? { issuer: issuer.trim() } : {}),
    ...(kind === "microsoft" ? { tenant: tenant.trim() || "common" } : {}),
    signup,
    domains: domains
      .split(/[\s,]+/)
      .map((d) => d.trim())
      .filter(Boolean),
  };
  const save = useMutation({
    mutationFn: () =>
      provider
        ? data(Auth.changeLoginProvider({ client, path: { key: provider.key }, body }))
        : data(Auth.addLoginProvider({ client, body: { ...body, kind } })),
    onSuccess: (p) => {
      onSaved();
      if (provider) onClose();
      else setSaved(p);
    },
  });
  const remove = useMutation({
    mutationFn: () => data(Auth.removeLoginProvider({ client, path: { key: provider!.key } })),
    onSuccess: () => {
      onSaved();
      onClose();
    },
  });
  const missing = !clientId.trim()
    ? "Enter the client id"
    : !provider && !secret.trim()
      ? "Enter the client secret"
      : kind === "oidc" && !issuer.trim()
        ? "Enter the issuer address"
        : null;
  const err = save.error?.message ?? remove.error?.message;
  const shown = provider ?? saved;

  return (
    <Dialog
      open
      wide
      onOpenChange={(o) => !o && onClose()}
      title={saved ? `${saved.label} is set up` : provider ? `Change ${provider.label}` : "Add a sign-in provider"}
      description={saved ? "Add this redirect URI at the provider, if you haven't yet." : undefined}
      actions={
        saved ? (
          <Button variant="primary" onClick={onClose}>
            Done
          </Button>
        ) : (
          <>
            {provider &&
              (confirmRemove ? (
                <Button
                  variant="danger"
                  className="mr-auto"
                  onClick={() => remove.mutate()}
                  disabled={remove.isPending}
                >
                  Remove, and disconnect {provider.people === 1 ? "1 person" : `${provider.people} people`}
                </Button>
              ) : (
                <Button variant="ghost" className="mr-auto" icon={<Trash2 />} onClick={() => setConfirmRemove(true)}>
                  Remove
                </Button>
              ))}
            <Button variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={() => save.mutate()}
              disabled={Boolean(missing) || save.isPending}
              disabledReason={missing ?? undefined}
            >
              {save.isPending ? "Saving…" : provider ? "Save" : "Add"}
            </Button>
          </>
        )
      }
    >
      <div className="flex flex-col gap-4">
        {!saved && (
          <>
            {!provider && (
              <Field label="Provider">
                {(f) => (
                  <Select id={f.id} value={kind} onChange={(e) => setKind(e.target.value as Kind)} options={KINDS} />
                )}
              </Field>
            )}
            <p className="text-[12.5px] leading-snug text-fg-secondary">Get a client id and secret at: {WHERE[kind]}</p>
            {kind === "oidc" && (
              <Field label="Issuer" hint="The provider's address; Lens reads /.well-known/openid-configuration there.">
                {(f) => (
                  <Input
                    id={f.id}
                    aria-describedby={f.describedBy}
                    value={issuer}
                    onChange={(e) => setIssuer(e.target.value)}
                    placeholder="https://auth.example.com/application/o/lens"
                  />
                )}
              </Field>
            )}
            {kind === "microsoft" && (
              <Field
                label="Directory (tenant) id"
                optional
                hint="Your organization's tenant lets Lens trust its email addresses. Empty (common) lets anyone sign in with an account already connected."
              >
                {(f) => (
                  <Input
                    id={f.id}
                    aria-describedby={f.describedBy}
                    value={tenant}
                    onChange={(e) => setTenant(e.target.value)}
                    placeholder="common"
                  />
                )}
              </Field>
            )}
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Client id">
                {(f) => (
                  <Input id={f.id} value={clientId} onChange={(e) => setClientId(e.target.value)} autoComplete="off" />
                )}
              </Field>
              <Field label="Client secret" hint={provider?.secret_set ? "Kept; leave empty to keep it" : undefined}>
                {(f) => (
                  <Input
                    id={f.id}
                    aria-describedby={f.describedBy}
                    type="password"
                    value={secret}
                    onChange={(e) => setSecret(e.target.value)}
                    autoComplete="new-password"
                  />
                )}
              </Field>
            </div>
            <Field
              label="Button name"
              optional
              hint={`“Continue with ${label.trim() || KINDS.find((k) => k.value === kind)!.label.split(" (")[0]}”`}
            >
              {(f) => (
                <Input
                  id={f.id}
                  aria-describedby={f.describedBy}
                  value={label}
                  maxLength={60}
                  onChange={(e) => setLabel(e.target.value)}
                />
              )}
            </Field>
            <Switch
              checked={signup}
              onCheckedChange={setSignup}
              label="Let people without a Lens account sign up (no roles until someone adds them)"
            />
            {signup && (
              <Field
                label="Only from these email domains"
                optional
                hint="Comma-separated, like example.com. Empty: anyone."
              >
                {(f) => (
                  <Input
                    id={f.id}
                    aria-describedby={f.describedBy}
                    value={domains}
                    onChange={(e) => setDomains(e.target.value)}
                    placeholder="example.com"
                  />
                )}
              </Field>
            )}
          </>
        )}
        {shown ? (
          <div className="flex flex-col gap-1.5">
            <span className="text-[13px] font-bold text-fg-strong">Redirect URI</span>
            <CodeBlock text={callbackUrl(shown.callback_path)} label="redirect URI" />
            <span className="text-[12px] text-fg-muted">
              Register it for each address people open Lens at (for example its https:// address).
            </span>
          </div>
        ) : (
          <span className="text-[12px] text-fg-muted">
            Redirect URI to register:{" "}
            <code className="font-mono">
              {callbackUrl(`/api/v1/auth/external/${kind === "oidc" ? "<name>" : kind}/callback`)}
            </code>
          </span>
        )}
        {err && <Banner tone="error">{err}</Banner>}
      </div>
    </Dialog>
  );
}
