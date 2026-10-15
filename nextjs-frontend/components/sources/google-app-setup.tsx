"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Auth } from "@/app/openapi-client";
import type { SignInInfo } from "@/app/openapi-client/types.gen";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import { CodeBlock } from "@/components/ui/states";
import { data, useApiClient } from "@/lib/api/browser";

/**
 * Signing in with Google needs the admin's own Google app (an OAuth client), once. It's the app people can sign in to
 * Lens with too (Settings › Sign-in), saved here with that left off, so the redirect URI is the sign-in's own.
 */
export function GoogleAppSetup({ signin }: { signin: SignInInfo }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const [id, setId] = useState("");
  const [secret, setSecret] = useState("");
  const save = useMutation({
    mutationFn: () =>
      data(
        Auth.addLoginProvider({
          client,
          body: { kind: "google", client_id: id.trim(), client_secret: secret.trim(), enabled: false },
        }),
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["source-backends"] });
      void qc.invalidateQueries({ queryKey: ["login-providers"] });
    },
  });
  const redirect =
    typeof window === "undefined" ? signin.callback_path : `${window.location.origin}${signin.callback_path}`;
  const ready = Boolean(id.trim() && secret.trim());
  const enter = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && ready) {
      e.preventDefault();
      save.mutate();
    }
  };
  return (
    <div className="flex flex-col gap-2.5 rounded-sm border border-border bg-surface px-3.5 py-3">
      <p className="text-[13px] font-semibold text-fg">To sign in with Google, add your Google app once</p>
      <ol className="list-decimal pl-5 text-[12.5px] leading-normal text-fg-secondary">
        <li>In Google Cloud console › APIs &amp; Services, enable the Google Drive API.</li>
        <li>
          On the OAuth consent screen, publish the app. While it’s in testing, Google signs Lens out after 7 days.
        </li>
        <li>Credentials › Create OAuth client ID › Web application, with this redirect URI:</li>
      </ol>
      <CodeBlock text={redirect} label="redirect URI" className="[&_pre]:py-2" />
      <div className="grid grid-cols-2 gap-2.5">
        <Field label="Client ID">
          {(f) => (
            <Input
              id={f.id}
              value={id}
              onChange={(e) => setId(e.target.value)}
              onKeyDown={enter}
              autoComplete="off"
              spellCheck={false}
            />
          )}
        </Field>
        <Field label="Client secret">
          {(f) => (
            <Input
              id={f.id}
              type="password"
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
              onKeyDown={enter}
              autoComplete="new-password"
            />
          )}
        </Field>
      </div>
      {save.error && (
        <p role="alert" className="text-[12.5px] text-red-dark">
          {save.error.message}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2.5">
        <Button
          size="sm"
          variant="secondary"
          onClick={() => save.mutate()}
          disabled={!ready || save.isPending}
          disabledReason="Enter the client ID and secret"
        >
          {save.isPending ? "Saving…" : "Save app"}
        </Button>
        <span className="text-[12px] leading-snug text-fg-muted">
          Signing in to Lens with Google stays off until you turn it on in Settings › Sign-in.
        </span>
      </div>
    </div>
  );
}
