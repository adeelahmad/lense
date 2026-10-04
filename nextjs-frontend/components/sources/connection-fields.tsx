"use client";

import { useState } from "react";

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
} from "@/components/sources/source-model";
import { TokenPaste } from "@/components/sources/token-paste";
import { Button } from "@/components/ui/button";
import { Field, Input, SecretField, Select, Textarea } from "@/components/ui/field";
import { Segmented } from "@/components/ui/tabs";

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
}) {
  const [pasting, setPasting] = useState(Boolean(replaceToken));
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
    case "drive":
      return (
        <div className="flex flex-col gap-3">
          {token}
          {select("scope", DRIVE_SCOPES)}
          {text("root_folder_id", {
            mono: true,
            label: "Root folder ID",
            optional: true,
            hint: "From the folder’s URL; limits the connection to that folder",
          })}
        </div>
      );
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
