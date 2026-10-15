"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Sources } from "@/app/openapi-client";
import {
  DRIVE_SCOPES,
  FIELD_LABEL,
  IMAP_SECURITY,
  S3_PROVIDERS,
  WEBDAV_VENDORS,
  type BackendSpec,
  type ConnForm,
  type SourceType,
  imapHost,
  parseServiceAccountKey,
} from "@/components/sources/source-model";
import { TokenPaste } from "@/components/sources/token-paste";
import { Button } from "@/components/ui/button";
import { Field, Input, SecretField, Select, Textarea } from "@/components/ui/field";
import { CodeBlock } from "@/components/ui/states";
import { Segmented } from "@/components/ui/tabs";
import { data, useApiClient } from "@/lib/api/browser";

const SERVICE: Partial<Record<SourceType, string>> = {
  dropbox: "Dropbox",
  drive: "Google",
  onedrive: "Microsoft",
};

/** SO2 step 2: the details for one type of connection, from the backend's field list, with type-specific widgets. */
export function ConnectionFields({
  type,
  spec,
  form,
  onChange,
  errors,
  saved,
  isSet,
  replaceToken,
  onConnectGoogle,
  connectingGoogle,
}: {
  type: SourceType;
  spec: BackendSpec;
  form: ConnForm;
  onChange: (f: ConnForm) => void;
  errors: Record<string, string>;
  /** The connection exists (editing it, or created earlier in the dialog): saved secrets show as set / replace / clear. */
  saved: boolean;
  isSet: (key: string) => boolean;
  replaceToken?: boolean;
  /** Connect with Google (Google Calendar): save the connection and go to Google's consent screen, through this OAuth
   * client when one is given, else the one Lens has. */
  onConnectGoogle?: (client: { client_id?: string; client_secret?: string }) => void;
  connectingGoogle?: boolean;
}) {
  const api = useApiClient();
  const [pasting, setPasting] = useState(Boolean(replaceToken));
  const [ownClient, setOwnClient] = useState(false);
  const [oauthClient, setOauthClient] = useState({ client_id: "", client_secret: "" });
  const googleClient = useQuery({
    queryKey: ["google-client"],
    queryFn: () => data(Sources.googleClient({ client: api })),
    enabled: type === "gcal",
    staleTime: 60_000,
  });
  const editing = saved;
  const p = form.params;
  const set = (k: string, v: string) => onChange({ ...form, params: { ...p, [k]: v } });
  const setSecret = (k: string, v: string | undefined) => onChange({ ...form, secrets: { ...form.secrets, [k]: v } });

  const text = (
    k: string,
    opts: {
      mono?: boolean;
      placeholder?: string;
      hint?: string;
      label?: string;
      optional?: boolean;
      onValue?: (v: string) => void;
    } = {},
  ) => (
    <Field
      key={k}
      label={opts.label ?? FIELD_LABEL[k] ?? k}
      hint={opts.hint}
      error={errors[k]}
      optional={opts.optional}
    >
      {({ id, describedBy, invalid }) => (
        <Input
          id={id}
          aria-describedby={describedBy}
          invalid={invalid}
          mono={opts.mono}
          value={p[k] ?? ""}
          placeholder={opts.placeholder}
          onChange={(e) => (opts.onValue ?? ((v: string) => set(k, v)))(e.target.value)}
          autoComplete="off"
          spellCheck={false}
        />
      )}
    </Field>
  );
  const select = (k: string, options: { value: string; label: string }[]) => (
    <Field key={k} label={FIELD_LABEL[k] ?? k} error={errors[k]}>
      {({ id, describedBy }) => (
        <Select
          id={id}
          aria-describedby={describedBy}
          value={p[k] ?? ""}
          onChange={(e) => set(k, e.target.value)}
          options={options}
        />
      )}
    </Field>
  );
  const secret = (k: string, opts: { hint?: string; multiline?: boolean; label?: string } = {}) => (
    <Field
      key={k}
      label={opts.label ?? FIELD_LABEL[k] ?? k}
      hint={editing ? undefined : (opts.hint ?? "Never shown again after saving")}
      error={errors[k]}
    >
      {({ id, describedBy, invalid }) =>
        editing && isSet(k) ? (
          <SecretField id={id} isSet value={form.secrets[k]} onChange={(v) => setSecret(k, v)} />
        ) : opts.multiline ? (
          <Textarea
            id={id}
            aria-describedby={describedBy}
            invalid={invalid}
            mono
            rows={3}
            className="min-h-[64px] text-[12px]"
            placeholder="Paste the whole private key file (OpenSSH or PEM)"
            value={form.secrets[k] ?? ""}
            onChange={(e) => setSecret(k, e.target.value || undefined)}
            spellCheck={false}
          />
        ) : (
          <Input
            id={id}
            aria-describedby={describedBy}
            invalid={invalid}
            type="password"
            autoComplete="new-password"
            value={form.secrets[k] ?? ""}
            onChange={(e) => setSecret(k, e.target.value || undefined)}
          />
        )
      }
    </Field>
  );

  const token = spec.oauth ? (
    editing && isSet("token") && !pasting ? (
      <div className="flex flex-wrap items-center gap-2 rounded-sm border border-border bg-surface px-3.5 py-2.5 text-[13px] text-fg-secondary">
        <span className="flex-1">A token is saved (never shown again). rclone refreshes it while it can.</span>
        <Button size="sm" variant="secondary" onClick={() => setPasting(true)}>
          Paste new token
        </Button>
      </div>
    ) : (
      <div className="flex flex-col gap-1">
        <TokenPaste
          command={spec.oauth}
          service={SERVICE[type] ?? "your account"}
          value={form.tokenText}
          onChange={(v) => onChange({ ...form, tokenText: v })}
          replacing={editing}
        />
        {errors.token && form.tokenText.trim() === "" && (
          <p role="alert" className="text-[12.5px] text-red-dark">
            {errors.token}
          </p>
        )}
      </div>
    )
  ) : null;

  /** A Google service account's .json key file (Google Drive, Google Calendar). */
  const serviceAccountKey = (share: string) => {
    const keyText = form.secrets.service_account_credentials;
    const key = keyText ? parseServiceAccountKey(keyText) : null;
    return (
      <Field
        label={FIELD_LABEL.service_account_credentials}
        hint={
          key?.ok
            ? `Signs in as ${key.email}. ${share}`
            : editing && isSet("service_account_credentials") && keyText === undefined
              ? "A key is saved (never shown again). Choose a file to replace it."
              : "The .json key file from Google Cloud (IAM › Service accounts › Keys). Never shown again after saving"
        }
        error={errors.service_account_credentials}
      >
        {({ id, describedBy }) => (
          <input
            id={id}
            aria-describedby={describedBy}
            type="file"
            accept=".json,application/json"
            className="text-[13px]"
            onChange={async (e) => {
              const f = e.target.files?.[0];
              setSecret("service_account_credentials", f ? await f.text() : undefined);
            }}
          />
        )}
      </Field>
    );
  };

  /** Google Calendar as a Google account: Connect with Google (Google's consent screen), or an authorized user JSON
   * pasted under the fold. */
  const googleAccount = (() => {
    const g = googleClient.data;
    const connected = editing && isSet("token") && form.secrets.token === undefined;
    const needClient = Boolean(g) && (g?.origin == null || ownClient);
    const pasted = form.secrets.token ?? "";
    return (
      <div className="flex flex-col gap-3">
        {connected && (
          <p className="rounded-sm border border-border bg-surface px-3.5 py-2.5 text-[13px] text-fg-secondary">
            {p.account ? (
              <>
                Connected as <strong className="font-semibold text-fg">{p.account}</strong>.
              </>
            ) : (
              "A Google account is connected."
            )}
          </p>
        )}
        {needClient && g && (
          <>
            <div className="grid grid-cols-2 gap-2.5">
              <Field label="OAuth client ID">
                {({ id }) => (
                  <Input
                    id={id}
                    mono
                    autoComplete="off"
                    placeholder="…apps.googleusercontent.com"
                    value={oauthClient.client_id}
                    onChange={(e) => setOauthClient({ ...oauthClient, client_id: e.target.value })}
                  />
                )}
              </Field>
              <Field label="Client secret">
                {({ id }) => (
                  <Input
                    id={id}
                    type="password"
                    autoComplete="off"
                    value={oauthClient.client_secret}
                    onChange={(e) => setOauthClient({ ...oauthClient, client_secret: e.target.value })}
                  />
                )}
              </Field>
            </div>
            <div className="flex flex-col gap-1.5 text-[12.5px] leading-normal text-fg-secondary">
              <span>
                In Google Cloud › APIs &amp; Services: enable the Google Calendar API, then under Credentials create an
                OAuth client ID (a web application) with this authorized redirect URI. Lens keeps the client with the
                connection.
              </span>
              <CodeBlock inline text={g.redirect_uri} label="redirect URI" />
            </div>
          </>
        )}
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <Button
            variant={connected ? "secondary" : "primary"}
            disabled={
              !g ||
              connectingGoogle ||
              (needClient && !(oauthClient.client_id.trim() && oauthClient.client_secret.trim()))
            }
            onClick={() =>
              onConnectGoogle?.(
                needClient
                  ? { client_id: oauthClient.client_id.trim(), client_secret: oauthClient.client_secret.trim() }
                  : {},
              )
            }
          >
            {connectingGoogle ? "Opening Google…" : connected ? "Connect again" : "Connect with Google"}
          </Button>
          {g?.origin && !ownClient && (
            <span className="text-[12px] text-fg-muted">
              Through {g.origin === "sign-in" ? "Google sign-in’s" : "an earlier connection’s"} OAuth client ·{" "}
              <Button size="xs" variant="link" onClick={() => setOwnClient(true)}>
                use another
              </Button>
            </span>
          )}
        </div>
        {errors.token && !pasted.trim() && (
          <p role="alert" className="text-[12.5px] text-red-dark">
            {errors.token}
          </p>
        )}
        <details open={Boolean(pasted) || undefined} className="text-[13px]">
          <summary className="cursor-pointer font-semibold text-fg-secondary">Or paste an authorized user JSON</summary>
          <div className="mt-2">
            <Field
              hint="client_id, client_secret and refresh_token of an OAuth client of yours with the calendar.readonly scope, as gcloud auth application-default login --client-id-file writes it. Never shown again after saving"
              error={pasted.trim() ? errors.token : undefined}
            >
              {({ id, describedBy, invalid }) => (
                <Textarea
                  id={id}
                  aria-label="Authorized user JSON"
                  aria-describedby={describedBy}
                  invalid={invalid}
                  mono
                  rows={3}
                  className="min-h-[64px] text-[12px]"
                  placeholder='{"client_id": "…", "client_secret": "…", "refresh_token": "…"}'
                  value={pasted}
                  onChange={(e) => setSecret("token", e.target.value || undefined)}
                  spellCheck={false}
                />
              )}
            </Field>
          </div>
        </details>
      </div>
    );
  })();

  switch (type) {
    case "s3":
      return (
        <div className="flex flex-col gap-3">
          <div className="grid grid-cols-2 gap-2.5">
            {select("provider", S3_PROVIDERS)}
            {text("region", { placeholder: "eu-central-1" })}
          </div>
          {text("endpoint", {
            mono: true,
            placeholder: p.provider === "AWS" ? "Leave empty for AWS" : "https://s3.example.com",
            optional: p.provider === "AWS",
          })}
          {text("access_key_id", { mono: true })}
          {secret("secret_access_key")}
        </div>
      );
    case "dropbox":
      return token;
    case "drive": {
      const serviceAccount = serviceAccountKey("Share the folders Lens should read with that address.");
      return (
        <div className="flex flex-col gap-3">
          <Segmented
            className="w-full [&>button]:flex-1 [&>button]:justify-center"
            value={form.driveAuth}
            onChange={(v) => onChange({ ...form, driveAuth: v as ConnForm["driveAuth"] })}
            items={[
              { value: "token", label: "Google account" },
              { value: "service_account", label: "Service account" },
            ]}
          />
          {form.driveAuth === "service_account" ? serviceAccount : token}
          {select("scope", DRIVE_SCOPES)}
          {text("root_folder_id", {
            mono: true,
            label: "Root folder",
            optional: true,
            placeholder: "Folder link or ID",
            hint: "Paste the folder’s link from Drive; the connection only sees that folder",
          })}
          {form.driveAuth === "service_account" && (
            <div className="grid grid-cols-2 gap-2.5">
              {text("team_drive", {
                mono: true,
                optional: true,
                placeholder: "Shared drive link or ID",
                hint: "Read a shared drive the account is a member of",
              })}
              {text("impersonate", {
                optional: true,
                placeholder: "you@company.com",
                hint: "Workspace only: needs domain-wide delegation",
              })}
            </div>
          )}
        </div>
      );
    }
    case "onedrive":
      return (
        <div className="flex flex-col gap-3">
          {token}
          <div className="grid grid-cols-2 gap-2.5">
            {text("drive_id", {
              mono: true,
              optional: true,
              hint: "rclone config shows it",
            })}
            {text("drive_type", {
              optional: true,
              placeholder: "personal, business…",
            })}
          </div>
        </div>
      );
    case "sftp":
      return (
        <div className="flex flex-col gap-3">
          <div className="grid grid-cols-[1fr_90px] gap-2.5">
            {text("host", { mono: true, placeholder: "calls-gw.internal" })}
            {text("port", { placeholder: "22" })}
          </div>
          {text("user", { mono: true })}
          <Segmented
            className="w-full [&>button]:flex-1 [&>button]:justify-center"
            value={form.sftpAuth}
            onChange={(v) => onChange({ ...form, sftpAuth: v as ConnForm["sftpAuth"] })}
            items={[
              { value: "pass", label: "Password" },
              { value: "key_pem", label: "Private key" },
            ]}
          />
          {form.sftpAuth === "pass"
            ? secret("pass")
            : secret("key_pem", {
                multiline: true,
                hint: "The whole key, including the BEGIN and END lines",
              })}
        </div>
      );
    case "smb":
      return (
        <div className="flex flex-col gap-3">
          {text("host", { mono: true, placeholder: "nas.office.lan" })}
          <div className="grid grid-cols-2 gap-2.5">
            {text("user")}
            {text("domain", { optional: true })}
          </div>
          {secret("pass")}
        </div>
      );
    case "webdav":
      return (
        <div className="flex flex-col gap-3">
          {text("url", {
            mono: true,
            placeholder: "https://cloud.example.com/remote.php/dav/files/lens",
          })}
          {select("vendor", WEBDAV_VENDORS)}
          <div className="grid grid-cols-2 gap-2.5">
            {text("user")}
            {secret("pass")}
          </div>
        </div>
      );
    case "imap":
      return (
        <div className="flex flex-col gap-3">
          <div className="grid grid-cols-2 gap-2.5">
            {text("user", {
              mono: true,
              placeholder: "you@example.com",
              // the server follows from the address until it's changed by hand
              onValue: (v) =>
                onChange({
                  ...form,
                  params: {
                    ...p,
                    user: v,
                    ...(!p.host || p.host === imapHost(p.user ?? "") ? { host: imapHost(v) } : {}),
                  },
                }),
            })}
            {secret("pass", { hint: "An app password where the provider has them; never shown again after saving" })}
          </div>
          <div className="grid grid-cols-[1fr_90px] gap-2.5">
            {text("host", { mono: true, placeholder: "imap.example.com" })}
            {text("port", { placeholder: "993" })}
          </div>
          {select("security", IMAP_SECURITY)}
          <p className="text-[12.5px] leading-normal text-fg-secondary">
            Mailboxes are folders and each message is an email. Lens only reads: messages stay unread and nothing is
            moved or deleted.
          </p>
        </div>
      );
    case "ical":
      return (
        <div className="flex flex-col gap-3">
          {secret("url", {
            label: "Calendar address",
            hint: "The iCal (.ics) link the calendar shares, https:// or webcal://. Kept like a password: never shown again",
          })}
          <div className="grid grid-cols-2 gap-2.5">
            {text("user", { optional: true })}
            {secret("pass", { label: "Password", hint: "Only if the address asks for one" })}
          </div>
          <p className="text-[12.5px] leading-normal text-fg-secondary">
            Each event becomes text: its title, when and where, who, and its description. An event that changes is read
            again.
          </p>
        </div>
      );
    case "gcal":
      return (
        <div className="flex flex-col gap-3">
          <Segmented
            className="w-full [&>button]:flex-1 [&>button]:justify-center"
            value={form.driveAuth}
            onChange={(v) => onChange({ ...form, driveAuth: v as ConnForm["driveAuth"] })}
            items={[
              { value: "token", label: "Google account" },
              { value: "service_account", label: "Service account" },
            ]}
          />
          {form.driveAuth === "service_account" ? (
            <>
              {serviceAccountKey("Share the calendars Lens should read with that address, and enter their IDs below.")}
              {text("impersonate", {
                optional: true,
                placeholder: "you@company.com",
                hint: "Workspace only: read this user’s calendars (needs domain-wide delegation)",
              })}
            </>
          ) : (
            googleAccount
          )}
          {text("calendars", {
            mono: true,
            optional: true,
            placeholder: "team@group.calendar.google.com",
            hint: "Calendar IDs, comma separated (Google Calendar › Settings › Integrate calendar). Empty: every calendar in the account",
          })}
          {text("days_back", {
            optional: true,
            placeholder: "365",
            hint: "Leave out events that ended more than this many days ago. Empty: every event",
          })}
          <p className="text-[12.5px] leading-normal text-fg-secondary">
            Each calendar is a folder and each event becomes text: its title, when and where, who, how it repeats, and
            its description. Lens only reads; an event that changes is read again.
          </p>
        </div>
      );
    default:
      return (
        <p className="rounded-sm border border-dashed border-border bg-surface px-3.5 py-3 text-[13px] leading-normal text-fg-secondary">
          Only folders allowed in the server config can be used (
          <code className="font-mono text-[12px]">sources.local_roots</code>, set at startup). You pick the folder when
          you watch it: Browse folders lists the allowed ones.
        </p>
      );
  }
}
