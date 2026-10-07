"use client";

import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { PlugZap, RefreshCw, Terminal } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, type ReactNode } from "react";

import { Admin, Fedora, Metadata, Sensors, Sources } from "@/app/openapi-client";
import type { MatterbridgeStatus } from "@/app/openapi-client/types.gen";
import { ComponentsStatus } from "@/components/settings/components-status";
import { AllTokens } from "@/components/account/all-tokens";
import { ACCESS } from "@/components/iiif/metadata-model";
import { RIGHTS } from "@/components/iiif/rights";
import { hubText } from "@/components/sensors/sensor-model";
import { SecretSetting, SettingField, ZoneBar, type FieldState } from "@/components/settings/fields";
import { SignInProviders } from "@/components/settings/sign-in-providers";
import { AddConnection } from "@/components/storage/add-connection";
import { NOT_STORAGE, fileStoreTry } from "@/components/storage/file-store";
import {
  AI_TOOLS,
  SPEECH_PROVIDERS,
  type FieldSpec,
  type SectionId,
  type SettingsView,
} from "@/components/settings/model";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Select, Switch } from "@/components/ui/field";
import { Skeleton } from "@/components/ui/states";
import { data, useApiClient } from "@/lib/api/browser";
import { useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

export type NsAccess = Record<string, string>;
export type BodyCtx = {
  section: SectionId;
  view: SettingsView;
  form: Record<string, unknown>;
  values: Record<string, unknown>;
  spec: (id: string) => FieldSpec;
  state: (id: string) => FieldState;
  nsAccess: NsAccess;
  setNsAccess: (ns: string, v: string) => void;
  initNsAccess: (m: NsAccess) => void;
  dirty: boolean;
};

function F({
  ctx,
  id,
  label,
  hint,
  options,
  className,
}: {
  ctx: BodyCtx;
  id: string;
  label?: ReactNode;
  hint?: ReactNode;
  options?: FieldSpec["options"];
  className?: string;
}) {
  const spec = ctx.spec(id);
  return (
    <SettingField
      spec={options ? { ...spec, options } : spec}
      state={ctx.state(id)}
      label={label}
      hint={hint}
      className={className}
    />
  );
}

function Sub({ children }: { children: ReactNode }) {
  return (
    <h3 className="border-t border-border pt-4 text-[15px] font-bold text-fg first:border-t-0 first:pt-0">
      {children}
    </h3>
  );
}

const LANGUAGES: [string, string][] = [
  ["auto", "Detect automatically"],
  ["en", "English"],
  ["de", "German"],
  ["pt", "Portuguese"],
  ["es", "Spanish"],
  ["fr", "French"],
  ["it", "Italian"],
  ["nl", "Dutch"],
  ["zh", "Chinese"],
  ["yue", "Cantonese"],
  ["ja", "Japanese"],
  ["ko", "Korean"],
];

const ENGINE_KEY: Record<string, [string, string]> = {
  sensevoice: ["sensevoice", "SenseVoice"],
  whisper: ["whisper", "Whisper"],
  "mlx-whisper": ["mlx_whisper", "mlx-whisper"],
};

export function SectionBody({ ctx }: { ctx: BodyCtx }) {
  const v = (id: string) => ctx.values[id];
  const raw = (id: string) => ctx.form[id];
  switch (ctx.section) {
    case "transcription": {
      const engine = String(raw("transcribe.engine") ?? "sensevoice");
      const provider = SPEECH_PROVIDERS.find((p) => p.id === engine);
      if (provider)
        return (
          <>
            <div className="grid gap-3 sm:grid-cols-2">
              <F ctx={ctx} id="transcribe.engine" />
              <F ctx={ctx} id="transcribe.language" options={LANGUAGES.map(([value, label]) => ({ value, label }))} />
            </div>
            <p className="text-[12.5px] leading-[1.45] text-fg-secondary">
              Recordings are sent to {provider.label}, which also tells speakers apart. Its address, model and key are
              in <Link href="/settings/speech-providers">Speech providers</Link>. SenseVoice and Whisper keep their
              models for when you switch back.
            </p>
          </>
        );
      const [key, name] = ENGINE_KEY[engine] ?? ENGINE_KEY.sensevoice;
      const lang = String(raw("transcribe.language") ?? "auto");
      const langs = LANGUAGES.some(([c]) => c === lang) ? LANGUAGES : [...LANGUAGES, [lang, lang] as [string, string]];
      const models = (["sensevoice", "whisper", "mlx_whisper"] as const).map(
        (k) =>
          `${ENGINE_KEY[k === "mlx_whisper" ? "mlx-whisper" : k][1]} · ${String(raw(`transcribe.${k}.model`) || "default")}`,
      );
      return (
        <>
          <div className="grid gap-3 sm:grid-cols-2">
            <F ctx={ctx} id="transcribe.engine" />
            <F ctx={ctx} id="transcribe.device" />
            <F ctx={ctx} id="transcribe.language" options={langs.map(([value, label]) => ({ value, label }))} />
            <F ctx={ctx} id={`transcribe.${key}.model`} label={`Model for ${name}`} />
          </div>
          <p className="text-[12.5px] leading-[1.45] text-fg-secondary">
            Each engine remembers its own model: {models.join(", ")}. Auto uses the fastest device each worker has.
          </p>
        </>
      );
    }
    case "speaker-separation":
      return (
        <>
          <F ctx={ctx} id="diarize.engine" />
          <div className="grid gap-3 sm:grid-cols-[1.2fr_1fr_1fr]">
            <F ctx={ctx} id="diarize.cluster_threshold" hint={undefined} />
            <F ctx={ctx} id="diarize.min_speakers" />
            <F ctx={ctx} id="diarize.max_speakers" />
          </div>
          {raw("diarize.engine") === "pyannote" && (
            <div className="grid gap-3 sm:grid-cols-2">
              <F ctx={ctx} id="diarize.pyannote.model" />
              <F ctx={ctx} id="diarize.pyannote.token_env" />
            </div>
          )}
          <p className="text-[12.5px] leading-[1.45] text-fg-secondary">
            By channel suits call recorders that put each side on its own channel. Speech provider uses the speakers a
            provider found while transcribing (Auto does too, when there are some). Off keeps one speaker per recording.
            Leave min and max empty to let it decide.
          </p>
        </>
      );
    case "speech-providers":
      return <SpeechProvidersBody ctx={ctx} />;
    case "voice-ids":
      return (
        <>
          <div className="flex flex-col gap-2.5 rounded-lg border border-border px-5 pb-4 pt-[18px]">
            <ZoneBar
              match={v("speakers.match_threshold") as number}
              review={v("speakers.review_threshold") as number}
            />
            <p className="text-[12.5px] leading-[1.45] text-fg-secondary">
              When valid, the three zones read: <b>new speaker</b> below review · <b>◆ review queue</b> between ·{" "}
              <b>✓ auto-match</b> at match and above.
            </p>
          </div>
          <div className="grid gap-3.5 sm:grid-cols-3">
            <F ctx={ctx} id="speakers.match_threshold" />
            <F ctx={ctx} id="speakers.review_threshold" />
            <F ctx={ctx} id="speakers.sample_seconds" />
          </div>
          <div className="border-t border-border py-3.5">
            <F ctx={ctx} id="speakers.cross_namespace" />
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <F ctx={ctx} id="speakers.embedder" />
            <F ctx={ctx} id="speakers.model" />
          </div>
        </>
      );
    case "analysis":
      return (
        <>
          <F ctx={ctx} id="analysis.entities" />
          {raw("analysis.entities") === "spacy" && <F ctx={ctx} id="analysis.spacy_model" />}
          <F ctx={ctx} id="analysis.anytopdf" />
          <F
            ctx={ctx}
            id="analysis.gazetteer"
            label={
              <>
                Custom vocabulary <span className="font-normal text-fg-muted">· one per line, Name | TYPE</span>
              </>
            }
            hint="Types: PERSON, ORG, PRODUCT, PLACE, EVENT, WORK, TERM. A line without a type is a term."
          />
        </>
      );
    case "llm":
      return <LlmBody ctx={ctx} />;
    case "local-model":
      return <LocalModelBody ctx={ctx} />;
    case "ai":
      return <AiBody ctx={ctx} />;
    case "search":
      return <SearchBody ctx={ctx} />;
    case "reports":
      return (
        <>
          <F ctx={ctx} id="reports.audio" />
          <div className="grid gap-3 sm:grid-cols-2">
            <F ctx={ctx} id="graph.max_nodes" />
            <F ctx={ctx} id="graph.min_edge_weight" />
          </div>
        </>
      );
    case "video":
      return (
        <>
          <Sub>Shots and keyframes</Sub>
          <div className="grid gap-3 sm:grid-cols-2">
            <F ctx={ctx} id="video.sample_seconds" />
            <F ctx={ctx} id="video.scene_threshold" />
            <F ctx={ctx} id="video.min_shot_seconds" />
            <F ctx={ctx} id="video.frame_width" />
          </div>
          <Sub>Text on screen</Sub>
          <F ctx={ctx} id="video.ocr_engine" />
          <div className="grid gap-3 sm:grid-cols-2">
            <F ctx={ctx} id="video.ocr_languages" />
            <F ctx={ctx} id="video.ocr_min_confidence" />
          </div>
          <Sub>Faces</Sub>
          <F ctx={ctx} id="video.face_engine" />
          <ZoneBar
            match={v("video.face_match_threshold") as number}
            review={v("video.face_review_threshold") as number}
            low="New face"
          />
          <div className="grid gap-3 sm:grid-cols-3">
            <F ctx={ctx} id="video.face_match_threshold" />
            <F ctx={ctx} id="video.face_review_threshold" />
            <F ctx={ctx} id="video.face_cluster_threshold" />
          </div>
          <div className="border-t border-border py-3.5">
            <F ctx={ctx} id="video.publish_faces" />
          </div>
          <Sub>Objects</Sub>
          <div className="grid gap-3 sm:grid-cols-2">
            <F ctx={ctx} id="video.object_engine" />
            <F ctx={ctx} id="video.object_min_score" />
          </div>
          <p className="text-[12.5px] text-fg-muted">
            YOLOX model:{" "}
            <code className="break-all font-mono text-fg-secondary">{ctx.view.bootstrap?.yolox_model ?? "—"}</code>{" "}
            (video.yolox_model, a startup setting; the lens:full image has one). Ultralytics is installed separately and
            is AGPL-3.0: a server that lets others use it must offer them its source.
          </p>
          <p className="text-[12.5px] text-fg-muted">
            Whether a namespace detects or recognises faces is set by its owner. How long face data is kept isn’t
            configurable yet.
          </p>
        </>
      );
    case "workers":
      return (
        <>
          <div className="grid gap-3 sm:grid-cols-2">
            <F ctx={ctx} id="workers.inline" />
            <F ctx={ctx} id="workers.poll_seconds" />
            <F ctx={ctx} id="workers.stale_minutes" />
            <F ctx={ctx} id="workers.max_attempts" />
          </div>
          <F ctx={ctx} id="workers.steps" />
          <p className="text-[12.5px] leading-[1.45] text-fg-muted">
            The number of built-in workers and their steps take effect when the server restarts. See which workers are
            alive in{" "}
            <Link href="/admin/health" className="font-semibold text-fg-accent hover:underline">
              System health
            </Link>
            .
          </p>
        </>
      );
    case "mail":
      return <MailBody ctx={ctx} />;
    case "bridge":
      return <BridgeBody ctx={ctx} />;
    case "remote-access":
      return <TunnelBody ctx={ctx} />;
    case "components":
      return (
        <>
          <F ctx={ctx} id="components.auto" />
          <ComponentsStatus ctx={ctx} />
        </>
      );
    case "access":
      return (
        <>
          <F ctx={ctx} id="server.allowed_hosts" />
          <F ctx={ctx} id="server.embed_frame_ancestors" />
          <F ctx={ctx} id="server.trusted_proxies" />
          <div className="grid items-end gap-3 sm:grid-cols-2">
            <F ctx={ctx} id="server.session_hours" />
            <div className="pb-2.5">
              <F ctx={ctx} id="server.secure_cookies" hint={undefined} />
            </div>
          </div>
          <F ctx={ctx} id="server.max_upload_mb" />
        </>
      );
    case "sign-in":
      return (
        <>
          <F ctx={ctx} id="auth.passwords" />
          <p className="text-[12.5px] leading-[1.45] text-fg-secondary">
            People add passkeys in their profile. Someone new, or who lost their passkey, gets a sign-in link from
            People (or <code className="font-mono text-[12px]">lens users link their@email</code> on the server).
          </p>
          <SignInProviders />
        </>
      );
    case "tokens":
      return (
        <>
          <div className="grid gap-3 sm:grid-cols-2">
            <F ctx={ctx} id="tokens.default_days" />
            <F ctx={ctx} id="tokens.max_days" />
          </div>
          <F ctx={ctx} id="tokens.never_expire" />
          <F ctx={ctx} id="tokens.oauth_enabled" />
          <div className="grid gap-3 sm:grid-cols-2">
            <F ctx={ctx} id="tokens.oauth_access_minutes" />
            <F ctx={ctx} id="tokens.oauth_refresh_days" />
          </div>
          <p className="text-[12.5px] leading-[1.45] text-fg-secondary">
            People make API keys on their API tokens page; a key acts as them, with their roles. These limits apply to
            new keys.
          </p>
          <AllTokens />
        </>
      );
    case "notifications":
      return (
        <>
          <F ctx={ctx} id="notifications.enabled" />
          <F ctx={ctx} id="notifications.networks" />
          <F ctx={ctx} id="notifications.app_url" />
          <div className="grid gap-3 sm:grid-cols-2">
            <F ctx={ctx} id="notifications.poll_seconds" />
            <F ctx={ctx} id="notifications.max_attempts" />
          </div>
          <p className="text-[12.5px] leading-[1.45] text-fg-secondary">
            Each namespace’s owners choose its targets and events on the namespace’s page. Workers send them, so a
            target must be reachable from where the workers run.
          </p>
        </>
      );
    case "encryption":
      return <EncryptionBody ctx={ctx} />;
    case "storage":
      return <StorageBody ctx={ctx} />;
    case "telemetry":
      return <TelemetryBody ctx={ctx} />;
    case "fedora":
      return <FedoraBody ctx={ctx} />;
    case "sensors":
      return <SensorsBody ctx={ctx} />;
    case "uploads":
      return (
        <>
          <div className="grid gap-3 sm:grid-cols-3">
            <F ctx={ctx} id="uploads.max_mb" />
            <F ctx={ctx} id="uploads.chunk_mb" />
            <F ctx={ctx} id="uploads.expire_hours" />
          </div>
          <F ctx={ctx} id="uploads.extensions" />
          <p className="text-[12.5px] leading-[1.45] text-fg-secondary">
            Uploads arrive in pieces and carry on where they stopped if the connection drops. Finished files are kept in
            the server’s data folder, under uploads. Transcript files have their own limit, under Access &amp;
            embedding; watched folders have no limit.
          </p>
        </>
      );
    case "documents":
      return (
        <>
          <div className="grid gap-3 sm:grid-cols-2">
            <F ctx={ctx} id="documents.page_pixels" />
            <F ctx={ctx} id="documents.thumb_pixels" />
            <F ctx={ctx} id="documents.ocr_below_chars" />
            <F ctx={ctx} id="documents.max_pages" />
          </div>
          <F ctx={ctx} id="documents.convert_seconds" />
          <F ctx={ctx} id="documents.attachment_resources" />
          <F ctx={ctx} id="documents.converter" />
          <F ctx={ctx} id="documents.anytopdf_url" />
          <F ctx={ctx} id="documents.anytopdf_token" />
          <ConversionNodeCheck ctx={ctx} />
          <Converters view={ctx.view} />
        </>
      );
    case "iiif":
      return <IiifBody ctx={ctx} />;
    case "startup":
      return <Startup view={ctx.view} />;
  }
}

function LlmBody({ ctx }: { ctx: BodyCtx }) {
  const client = useApiClient();
  const llm = ctx.view.llm?.values ?? {};
  const secret = (llm.api_key ?? {}) as { set?: boolean };
  const key = ctx.state("llm.api_key");
  const test = useMutation({
    mutationFn: () => data(Admin.testLlm({ client })),
  });
  return (
    <>
      <F ctx={ctx} id="llm.base_url" />
      <F ctx={ctx} id="llm.model" />
      <F ctx={ctx} id="llm.chat_models" />
      <div className="grid gap-3 sm:grid-cols-2">
        <F ctx={ctx} id="llm.vision_model" />
        <F ctx={ctx} id="llm.describe_max" />
      </div>
      <SecretSetting
        key={ctx.view.llm?.updated_at ?? "none"}
        label="API key"
        isSet={Boolean(secret.set)}
        updatedBy={ctx.view.llm?.updated_by}
        updatedAt={ctx.view.llm?.updated_at}
        value={key.value as string | undefined}
        onChange={(x) => key.onChange(x)}
      />
      <F ctx={ctx} id="llm.api_key_env" hint="Used only when no key is stored above" />
      <div className="grid gap-3 sm:grid-cols-2">
        <F ctx={ctx} id="llm.max_chars" />
        <F ctx={ctx} id="llm.timeout" />
      </div>
      <div className="flex flex-wrap items-center gap-2.5">
        <Button size="sm" icon={<PlugZap />} onClick={() => test.mutate()} disabled={test.isPending}>
          {test.isPending ? "Testing…" : "Test"}
        </Button>
        <span className="text-[12px] text-fg-muted">
          {ctx.dirty
            ? "Tests the saved settings, not your unsaved changes"
            : "Asks the model for one word and reports the latency"}
        </span>
      </div>
      {test.data &&
        (test.data.ok ? (
          <Banner tone="success" title="The model answered.">
            {test.data.model ?? "Model"} · {test.data.ms} ms
            {test.data.reply ? ` · replied “${test.data.reply}”` : ""}
          </Banner>
        ) : (
          <Banner tone="error" title="The test failed.">
            {test.data.error}
          </Banner>
        ))}
      {test.isError && <Banner tone="error">{test.error.message}</Banner>}
    </>
  );
}

function ConversionNodeCheck({ ctx }: { ctx: BodyCtx }) {
  const client = useApiClient();
  const test = useMutation({ mutationFn: () => data(Admin.testConversionNode({ client })) });
  if (!ctx.view.documents?.values?.anytopdf_url) return null;
  return (
    <>
      <div className="flex flex-wrap items-center gap-2.5">
        <Button size="sm" icon={<PlugZap />} onClick={() => test.mutate()} disabled={test.isPending}>
          {test.isPending ? "Checking…" : "Check it"}
        </Button>
        <span className="text-[12px] text-fg-muted">
          {ctx.dirty
            ? "Uses the saved settings, not your unsaved changes"
            : "Asks the conversion node for a job with Lens’s token"}
        </span>
      </div>
      {test.data &&
        (test.data.ok ? (
          <Banner tone="success" title="The conversion node answers.">
            Its address and token check out ({test.data.ms} ms).
          </Banner>
        ) : (
          <Banner tone="error" title="Not yet.">
            {test.data.error}
          </Banner>
        ))}
      {test.isError && <Banner tone="error">{test.error.message}</Banner>}
    </>
  );
}

function exportLine(e: { at: number; ok: boolean; error?: string | null } | null | undefined, what: string) {
  if (!e) return null;
  const when = new Date(e.at * 1000).toLocaleTimeString();
  return e.ok ? `${what} last sent at ${when}` : `${what} failed at ${when}: ${e.error ?? "refused"}`;
}

function TelemetryBody({ ctx }: { ctx: BodyCtx }) {
  const client = useApiClient();
  const t = ctx.view.telemetry;
  const saved = t?.values ?? {};
  const secret = (saved.headers ?? {}) as { set?: boolean };
  const headers = ctx.state("telemetry.headers");
  const locked = t?.locked ?? [];
  const status = useQuery({
    queryKey: ["telemetry-status", t?.updated_at ?? null],
    queryFn: () => data(Admin.telemetryStatus({ client })),
    refetchInterval: 30_000,
  });
  const test = useMutation({ mutationFn: () => data(Admin.testTelemetry({ client })) });
  const on = Boolean(saved.enabled);
  const where = (saved.endpoint as string | null | undefined) ?? null;
  const s = status.data;
  const lines = [exportLine(s?.last_traces, "Traces"), exportLine(s?.last_metrics, "Metrics")].filter(Boolean);
  const failed = Boolean((s?.last_traces && !s.last_traces.ok) || (s?.last_metrics && !s.last_metrics.ok));
  return (
    <>
      {on && where ? (
        <Banner tone={failed ? "error" : "success"} title={`On: sending to ${s?.endpoint ?? where}`}>
          {lines.length
            ? lines.join(" · ")
            : "Nothing sent from the server yet; the first export follows its first traced work."}
        </Banner>
      ) : on ? (
        <Banner tone="error" title="On, but there is nowhere to send to.">
          Set the endpoint below; until then nothing is collected or sent.
        </Banner>
      ) : (
        <Banner title="Off: nothing is collected or sent.">
          Lens never sends telemetry anywhere unless you turn it on here, in the setup wizard or with{" "}
          <code className="font-mono">LENS_TELEMETRY=on</code> in .env, and then only to the endpoint you set.
        </Banner>
      )}
      <F ctx={ctx} id="telemetry.enabled" />
      <F ctx={ctx} id="telemetry.endpoint" />
      <SecretSetting
        key={t?.updated_at ?? "none"}
        label={locked.includes("headers") ? "Headers (set by LENS_TELEMETRY_HEADERS in .env)" : "Headers"}
        placeholder="Authorization=Bearer%20token,X-Scope-OrgID=home"
        isSet={Boolean(secret.set)}
        updatedBy={t?.updated_by}
        updatedAt={t?.updated_at}
        value={headers.value as string | undefined}
        onChange={(x) => headers.onChange(x)}
      />
      <p className="-mt-2 text-[12px] text-fg-muted">
        Optional: key=value pairs, separated by commas and URL-encoded, for a collector that needs a token.
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        <F ctx={ctx} id="telemetry.traces" />
        <F ctx={ctx} id="telemetry.metrics" />
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <F ctx={ctx} id="telemetry.sample_ratio" />
        <F ctx={ctx} id="telemetry.export_seconds" />
        <F ctx={ctx} id="telemetry.service_name" />
      </div>
      <F ctx={ctx} id="telemetry.prices" />
      <p className="text-[12.5px] leading-[1.45] text-fg-secondary">
        What is sent: route templates (never paths or queries), job steps and how they ended, record ids, model names,
        token counts, durations and estimated cost. Never transcript, prompt or answer text, file names, titles,
        namespace names, people or addresses. Each worker follows these settings on its own.
      </p>
      <div className="flex flex-wrap items-center gap-2.5">
        <Button size="sm" icon={<PlugZap />} onClick={() => test.mutate()} disabled={test.isPending || !where}>
          {test.isPending ? "Sending…" : "Send a test span"}
        </Button>
        <span className="text-[12px] text-fg-muted">
          {ctx.dirty
            ? "Uses the saved settings, not your unsaved changes"
            : "Sends one span named lens.telemetry.test, even while telemetry is off"}
        </span>
      </div>
      {test.data &&
        (test.data.ok ? (
          <Banner tone="success" title="The endpoint took the test span.">
            {test.data.ms} ms
          </Banner>
        ) : (
          <Banner tone="error" title="The test failed.">
            {test.data.error}
          </Banner>
        ))}
      {test.isError && <Banner tone="error">{test.error.message}</Banner>}
    </>
  );
}

function EncryptionBody({ ctx }: { ctx: BodyCtx }) {
  const client = useApiClient();
  const progress = useQuery({
    queryKey: ["encryption-progress", ctx.view.encryption?.updated_at ?? null],
    queryFn: () => data(Admin.getEncryption({ client })),
    refetchInterval: (q) => (q.state.data?.running ? 2000 : false),
  });
  const p = progress.data;
  const on = Boolean(ctx.view.encryption?.values.files);
  return (
    <>
      {p?.running ? (
        <Banner title={p.to === "plain" ? "Turning files back to plain…" : "Encrypting the files already kept…"}>
          {p.changed} done so far. Lens keeps working meanwhile, and files that arrive now are already stored the new
          way.
        </Banner>
      ) : p?.error ? (
        <Banner tone="error" title={`Converting stopped after ${p.changed} file(s).`}>
          {p.error}. The rest are as they were; change the setting again, or run lens encrypt, to finish.
        </Banner>
      ) : p?.to ? (
        <Banner
          tone={p.skipped ? "warning" : "success"}
          title={`${p.changed} file(s) ${p.to === "plain" ? "turned back to plain" : "encrypted"}.`}
        >
          {p.skipped
            ? `${p.skipped} skipped: a vault nobody has unlocked, or a damaged file. They’re converted the next time this changes, or with lens encrypt.`
            : "Every file Lens keeps is stored the new way."}
        </Banner>
      ) : (
        <Banner title={on ? "Files are encrypted on disk." : "Files are kept as they are."}>
          Encrypted files still play, seek, download and are processed as before. Transcripts, search and the rest of
          the database aren’t covered; put the data volume on an encrypted disk for those.
        </Banner>
      )}
      <F ctx={ctx} id="encryption.files" />
      <div className="grid gap-3 sm:grid-cols-2">
        <F ctx={ctx} id="encryption.work_minutes" />
        <F ctx={ctx} id="encryption.vault_minutes" />
      </div>
      <p className="text-[12.5px] leading-[1.45] text-fg-secondary">
        Vaults are set on each namespace’s page: only their owners’ passkeys open them.
      </p>
    </>
  );
}

function StorageBody({ ctx }: { ctx: BodyCtx }) {
  const client = useApiClient();
  const conns = useQuery({ queryKey: ["sources"], queryFn: () => data(Sources.listSources({ client })) });
  const storage = (conns.data ?? []).filter((c) => !NOT_STORAGE.has(c.type));
  const store = ctx.state("files.store").value;
  const body = fileStoreTry(
    store,
    ctx.state("files.connection").value,
    ctx.state("files.folder").value,
    ctx.state("files.crypt").value,
  );
  const test = useMutation({ mutationFn: () => data(Admin.testFileStore({ client, body })) });
  const saved = ctx.view.files?.values ?? {};
  const savedConn = storage.find((c) => c.id === saved.connection);
  return (
    <>
      <Banner
        title={
          saved.store === "connection"
            ? `Files go to ${savedConn?.name ?? "a connection"}${saved.folder ? `, under ${saved.folder}` : ""}${saved.crypt ? ", through rclone crypt" : ""}.`
            : "Files are kept on this machine."
        }
      >
        Each file is encrypted with its namespace’s key before it leaves the machine, so a connection only ever holds
        ciphertext. Files already kept stay where they were put when this changes.
      </Banner>
      <F ctx={ctx} id="files.store" />
      {store === "connection" && (
        <>
          {conns.isSuccess && !storage.length ? (
            <Banner tone="error" title="No storage connection yet.">
              Add one (S3, Google Drive, Dropbox, OneDrive, SFTP, SMB or WebDAV); it’s picked here once it works.
            </Banner>
          ) : (
            <F
              ctx={ctx}
              id="files.connection"
              options={[
                { value: "", label: "Choose a connection" },
                ...storage.map((c) => ({ value: String(c.id), label: `${c.name} (${c.type})` })),
              ]}
            />
          )}
          <div>
            <AddConnection onAdded={(id) => ctx.state("files.connection").onChange(String(id))} />
          </div>
          <F ctx={ctx} id="files.folder" />
          <F ctx={ctx} id="files.crypt" />
        </>
      )}
      <div className="flex flex-wrap items-center gap-2.5">
        <Button size="sm" icon={<PlugZap />} onClick={() => test.mutate()} disabled={test.isPending}>
          {test.isPending ? "Checking…" : "Check it"}
        </Button>
        <span className="text-[12px] text-fg-muted">Writes a small file there, reads it back and removes it</span>
      </div>
      {test.data &&
        (test.data.ok ? (
          <Banner tone="success" title="It works.">
            Written, read back and removed in {test.data.seconds} s.
          </Banner>
        ) : (
          <Banner tone="error" title="That didn’t work.">
            {test.data.error}
          </Banner>
        ))}
      {test.isError && <Banner tone="error">{test.error.message}</Banner>}
    </>
  );
}

/** "Sent 12, 3 files, 140 unchanged" from the last sync's counts. */
export function fedoraCounts(c: Record<string, unknown> | null | undefined): string {
  if (!c) return "";
  const n = (k: string) => Number(c[k] ?? 0);
  return [
    `${n("sent")} sent`,
    n("files") ? `${n("files")} file${n("files") === 1 ? "" : "s"}` : "",
    `${n("unchanged")} unchanged`,
    n("deleted") ? `${n("deleted")} deleted` : "",
    n("failed") ? `${n("failed")} failed` : "",
  ]
    .filter(Boolean)
    .join(", ");
}

function FedoraBody({ ctx }: { ctx: BodyCtx }) {
  const client = useApiClient();
  const f = ctx.view.fedora;
  const saved = f?.values ?? {};
  const secret = (saved.password ?? {}) as { set?: boolean };
  const password = ctx.state("fedora.password");
  const locked = f?.locked ?? [];
  const status = useQuery({
    queryKey: ["fedora-status", f?.updated_at ?? null],
    queryFn: () => data(Fedora.getFedoraStatus({ client })),
    refetchInterval: 30_000,
  });
  const sync = useMutation({
    mutationFn: () => data(Fedora.syncFedora({ client })),
    onSuccess: () => void status.refetch(),
  });
  const s = status.data;
  const url = (saved.url as string | null | undefined) ?? null;
  return (
    <>
      {s?.enabled ? (
        <Banner tone={s.last_error ? "error" : "success"} title={`On: ${s.resources} resources kept in ${s.url}`}>
          {s.last_error
            ? `Last problem: ${s.last_error}`
            : s.last_sync
              ? `Last sent ${new Date(s.last_sync).toLocaleString()}: ${fedoraCounts(s.last_counts)}${s.pending ? ` · ${s.pending} waiting` : ""}`
              : "Nothing sent yet: the first sync sends everything."}
        </Banner>
      ) : (
        <Banner title="Off: the archive lives in SurrealDB only.">
          Run Fedora with <code className="font-mono">docker compose --profile fedora up</code>, then set its address
          here or with <code className="font-mono">LENS_FEDORA_URL</code> in .env. Lens keeps working the same without
          it.
        </Banner>
      )}
      <F ctx={ctx} id="fedora.url" />
      <F ctx={ctx} id="fedora.enabled" />
      <div className="grid gap-3 sm:grid-cols-2">
        <F ctx={ctx} id="fedora.user" />
        <SecretSetting
          key={f?.updated_at ?? "none"}
          label={locked.includes("password") ? "Password (set by LENS_FEDORA_PASSWORD in .env)" : "Password"}
          placeholder="fedoraAdmin"
          isSet={Boolean(secret.set)}
          updatedBy={f?.updated_by}
          updatedAt={f?.updated_at}
          value={password.value as string | undefined}
          onChange={(x) => password.onChange(x)}
        />
      </div>
      <F ctx={ctx} id="fedora.root" />
      <div className="grid gap-3 sm:grid-cols-2">
        <F ctx={ctx} id="fedora.files" />
        <F ctx={ctx} id="fedora.max_file_mb" />
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <F ctx={ctx} id="fedora.sync_seconds" />
        <F ctx={ctx} id="fedora.full_hours" />
      </div>
      <p className="text-[12.5px] leading-[1.45] text-fg-secondary">
        Each resource is described with Dublin Core and links back to its Lens address (owl:sameAs). Changes to a
        recording’s metadata go within a minute; everything is compared on the schedule above, so what analysis changed,
        new recordings and deletions follow. The workers send it.
      </p>
      <div className="flex flex-wrap items-center gap-2.5">
        <Button size="sm" icon={<RefreshCw />} onClick={() => sync.mutate()} disabled={sync.isPending || !url}>
          {sync.isPending ? "Sending…" : "Compare and send now"}
        </Button>
        <span className="text-[12px] text-fg-muted">
          {ctx.dirty ? "Uses the saved settings, not your unsaved changes" : "Sends only what differs from Fedora"}
        </span>
      </div>
      {sync.data && (
        <Banner tone={sync.data.failed ? "error" : "success"} title={fedoraCounts(sync.data)}>
          {sync.data.errors[0] ?? "Fedora has everything."}
        </Banner>
      )}
      {sync.isError && <Banner tone="error">{sync.error.message}</Banner>}
    </>
  );
}

function SensorsBody({ ctx }: { ctx: BodyCtx }) {
  const client = useApiClient();
  const status = useQuery({
    queryKey: ["sensors", ctx.view.sensors?.updated_at ?? null],
    queryFn: () => data(Sensors.listSensors({ client })),
    refetchInterval: 15_000,
  });
  const hub = status.data?.hub;
  const h = hub ? hubText(hub) : null;
  const on = ctx.values["sensors.enabled"] === true;
  return (
    <>
      {h && (
        <Banner
          tone={h.tone}
          title={h.title}
          action={
            <Button asChild size="xs" variant="secondary">
              <Link href="/sensors">See sensors</Link>
            </Button>
          }
        >
          {hub?.enabled ? h.body : "Turn it on below; the workers start listening within seconds."}
        </Banner>
      )}
      <F ctx={ctx} id="sensors.enabled" />
      <Sub>MQTT</Sub>
      <div className="grid gap-3 sm:grid-cols-2">
        <F ctx={ctx} id="sensors.mqtt" />
        <F ctx={ctx} id="sensors.mqtt_anonymous" />
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <F ctx={ctx} id="sensors.mqtt_port" />
        <F ctx={ctx} id="sensors.max_payload_kb" />
      </div>
      <Sub>Syslog</Sub>
      <div className="grid gap-3 sm:grid-cols-2">
        <F ctx={ctx} id="sensors.syslog" />
        <F ctx={ctx} id="sensors.syslog_port" />
      </div>
      <F ctx={ctx} id="sensors.syslog_networks" />
      <p className="text-[12.5px] leading-[1.45] text-fg-secondary">
        With Docker, the worker container publishes 1883 and 5514; set <code className="font-mono">LENS_MQTT_PORT</code>{" "}
        or <code className="font-mono">LENS_SYSLOG_PORT</code> in .env to publish them on other ports of the host.
        {on ? "" : " Nothing listens until the hub is on."}
      </p>
      <Sub>What’s kept</Sub>
      <p className="text-[12.5px] leading-[1.45] text-fg-secondary">
        Defaults for every sensor; each one can choose its own on its page. The hourly Tidy sensor data routine removes
        what’s past its time.
      </p>
      <F ctx={ctx} id="sensors.store" />
      <div className="grid gap-3 sm:grid-cols-3">
        <F ctx={ctx} id="sensors.raw_days" />
        <F ctx={ctx} id="sensors.important_days" />
        <F ctx={ctx} id="sensors.rollup_days" />
      </div>
      <F ctx={ctx} id="sensors.max_per_minute" />
      <F ctx={ctx} id="sensors.triage" />
    </>
  );
}

function MailBody({ ctx }: { ctx: BodyCtx }) {
  const client = useApiClient();
  const m = ctx.view.mail;
  const locked = m?.locked ?? [];
  const pw = ctx.state("mail.password");
  const test = useMutation({ mutationFn: () => data(Admin.testMail({ client })) });
  return (
    <>
      {locked.length > 0 && (
        <p className="text-[13px] text-fg-secondary">
          Some of these are set in the server’s .env (MAIL_*), which wins: {locked.join(", ")}.
        </p>
      )}
      <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_120px]">
        <F ctx={ctx} id="mail.server" />
        <F ctx={ctx} id="mail.port" />
      </div>
      <F ctx={ctx} id="mail.security" />
      <F ctx={ctx} id="mail.username" />
      {locked.includes("password") ? (
        <p className="text-[13px] text-fg-secondary">The password is set by MAIL_PASSWORD in .env.</p>
      ) : (
        <SecretSetting
          key={m?.updated_at ?? "none"}
          label="Password"
          isSet={Boolean(((m?.values?.password ?? {}) as { set?: boolean }).set)}
          updatedBy={m?.updated_by}
          updatedAt={m?.updated_at}
          value={pw.value as string | undefined}
          onChange={(x) => pw.onChange(x)}
        />
      )}
      <div className="grid gap-3 sm:grid-cols-2">
        <F ctx={ctx} id="mail.from_address" />
        <F ctx={ctx} id="mail.from_name" />
      </div>
      <div className="flex flex-wrap items-center gap-2.5">
        <Button size="sm" icon={<PlugZap />} onClick={() => test.mutate()} disabled={test.isPending}>
          {test.isPending ? "Sending…" : "Send a test email"}
        </Button>
        <span className="text-[12px] text-fg-muted">
          {ctx.dirty ? "Uses the saved settings, not your unsaved changes" : "To your own address"}
        </span>
      </div>
      {test.data &&
        (test.data.ok ? (
          <Banner tone="success" title="Sent.">
            Check {test.data.to} for “Lens can send email”.
          </Banner>
        ) : (
          <Banner tone="error" title="It couldn’t be sent.">
            {test.data.error}
          </Banner>
        ))}
      {test.isError && <Banner tone="error">{test.error.message}</Banner>}
    </>
  );
}

/** Settings › Remote access: the Cloudflare tunnel Lens runs, and how it's doing right now. */
function TunnelBody({ ctx }: { ctx: BodyCtx }) {
  const client = useApiClient();
  const t = ctx.view.tunnel;
  const mode = (ctx.state("tunnel.mode").value as string | undefined) ?? "off";
  const token = ctx.state("tunnel.token");
  const apiToken = ctx.state("tunnel.api_token");
  const status = useQuery({
    queryKey: ["tunnel-status", t?.updated_at ?? null],
    queryFn: () => data(Admin.tunnelStatus({ client })),
    refetchInterval: (q) => (q.state.data?.mode === "off" ? false : q.state.data?.connected ? 15_000 : 3_000),
  });
  const s = status.data;
  const secret = (k: string) => Boolean(((t?.values?.[k] ?? {}) as { set?: boolean }).set);
  return (
    <>
      {!s || s.mode === "off" ? (
        <Banner title="Off.">
          Lens is reached only where it runs. Pick a way below to reach it from anywhere at an https:// address, where
          passkeys work too.
        </Banner>
      ) : s.connected && s.url ? (
        <Banner tone="success" title="Reachable from anywhere.">
          <a href={s.url} target="_blank" rel="noreferrer" className="font-mono font-semibold hover:underline">
            {s.url}
          </a>
          {s.mode === "quick" && " · this address changes when the tunnel restarts."}
        </Banner>
      ) : s.error ? (
        <Banner
          tone="error"
          title={s.running ? "Not connected yet: cloudflared keeps trying." : "The tunnel isn’t up."}
        >
          {s.running && s.url ? s.error.replace(/\.?$/, ".") : s.error}
          {s.running && s.url && (
            <>
              {" "}
              It will answer at <span className="font-mono">{s.url}</span>.
            </>
          )}
        </Banner>
      ) : (
        <Banner title="Starting.">A server process starts cloudflared within a few seconds.</Banner>
      )}
      <F ctx={ctx} id="tunnel.mode" />
      {(mode === "managed" || mode === "token") && <F ctx={ctx} id="tunnel.hostname" />}
      {mode === "managed" && (
        <>
          <SecretSetting
            key={`api-${t?.updated_at ?? "none"}`}
            label="Cloudflare API token"
            placeholder="Paste a Cloudflare API token"
            isSet={secret("api_token")}
            updatedBy={t?.updated_by}
            updatedAt={t?.updated_at}
            value={apiToken.value as string | undefined}
            onChange={(x) => apiToken.onChange(x)}
          />
          <p className="text-[12.5px] leading-[1.45] text-fg-muted">
            Make one at Cloudflare › My Profile › API Tokens with Account › Cloudflare Tunnel › Edit, Zone › DNS › Edit
            and Zone › Zone › Read, for the domain the hostname is on. Lens makes the tunnel, points it at the web app
            and adds the hostname’s DNS record.
          </p>
        </>
      )}
      {mode === "token" && (
        <>
          <SecretSetting
            key={`token-${t?.updated_at ?? "none"}`}
            label="Tunnel token"
            placeholder="Paste the tunnel’s token"
            isSet={secret("token")}
            updatedBy={t?.updated_by}
            updatedAt={t?.updated_at}
            value={token.value as string | undefined}
            onChange={(x) => token.onChange(x)}
          />
          <p className="text-[12.5px] leading-[1.45] text-fg-muted">
            In the Cloudflare dashboard, add a public hostname to the tunnel that points at{" "}
            <code className="font-mono">{s?.origin ?? "the web app"}</code>, and give that hostname above.
          </p>
        </>
      )}
      {mode !== "off" && (
        <F
          ctx={ctx}
          id="tunnel.origin"
          hint={`Where cloudflared reaches the web app from the server${s ? ` (now ${s.origin})` : ""}. Leave empty unless you moved it.`}
        />
      )}
      {s && s.mode !== "off" && (s.log ?? []).length > 0 && (
        <details className="text-[12.5px]">
          <summary className="cursor-pointer font-semibold text-fg-secondary">
            What cloudflared said{s.process ? ` (in ${s.process})` : ""}
          </summary>
          <pre className="mt-2 max-h-64 overflow-auto rounded-sm bg-surface-neutral p-2.5 font-mono text-[11.5px] leading-snug">
            {(s.log ?? []).join("\n")}
          </pre>
        </details>
      )}
    </>
  );
}

function BridgeBody({ ctx }: { ctx: BodyCtx }) {
  const client = useApiClient();
  const b = ctx.view.bridge;
  const token = ctx.state("bridge.token");
  const status = useQuery({
    queryKey: ["bridge-status", b?.updated_at ?? null],
    queryFn: () => data(Admin.bridgeStatus({ client })),
    refetchInterval: (q) => (q.state.data?.matterbridge?.whatsapp_qr ? 3_000 : 10_000), // the QR code changes often
  });
  const test = useMutation({ mutationFn: () => data(Admin.testBridge({ client })) });
  const s = status.data;
  const managed = Boolean(ctx.form["matterbridge.run"]);
  return (
    <>
      {s?.state === "running" ? (
        <Banner tone="success" title="Listening in the bridged rooms.">
          {s.answered ? `Answered ${s.answered} message${s.answered === 1 ? "" : "s"} so far. ` : ""}
          Each person’s conversation is in Chat for the account it answers as.
        </Banner>
      ) : s?.state === "error" ? (
        <Banner tone="error" title="The last look at the rooms failed.">
          {s.error}
        </Banner>
      ) : s?.state === "incomplete" ? (
        <Banner tone="error" title="On, but not ready.">
          It needs {s.error}.
        </Banner>
      ) : s?.state === "starting" ? (
        <Banner title="Starting.">A server process picks it up within a few seconds.</Banner>
      ) : (
        <Banner title="Off.">
          Turn on Run Matterbridge here and add your chat networks below, or use a Matterbridge of your own with an API
          account in the same gateway as your rooms. Then turn this on.
        </Banner>
      )}
      <F ctx={ctx} id="bridge.enabled" />
      <F
        ctx={ctx}
        id="matterbridge.run"
        hint="Lens writes Matterbridge’s config from the networks below. Needs the matterbridge service: docker compose --profile matterbridge up -d (or COMPOSE_PROFILES=matterbridge in .env)."
      />
      {managed ? (
        <RunMatterbridge ctx={ctx} status={s?.matterbridge ?? null} />
      ) : (
        <>
          <F ctx={ctx} id="bridge.url" />
          <SecretSetting
            key={b?.updated_at ?? "none"}
            label="API token"
            isSet={Boolean(((b?.values?.token ?? {}) as { set?: boolean }).set)}
            updatedBy={b?.updated_by}
            updatedAt={b?.updated_at}
            value={token.value as string | undefined}
            onChange={(x) => token.onChange(x)}
          />
        </>
      )}
      <F ctx={ctx} id="bridge.account" />
      <div className="grid gap-3 sm:grid-cols-2">
        <F ctx={ctx} id="bridge.name" />
        <F ctx={ctx} id="bridge.answer" />
      </div>
      <F ctx={ctx} id="bridge.gateway" />
      <F ctx={ctx} id="bridge.users" />
      <F ctx={ctx} id="bridge.approve" />
      <F ctx={ctx} id="bridge.rooms" />
      <F ctx={ctx} id="bridge.poll_seconds" />
      <div className="flex flex-wrap items-center gap-2.5">
        <Button size="sm" icon={<PlugZap />} onClick={() => test.mutate()} disabled={test.isPending}>
          {test.isPending ? "Checking…" : "Check the connection"}
        </Button>
        {ctx.dirty && (
          <span className="text-[12px] text-fg-muted">Uses the saved settings, not your unsaved changes</span>
        )}
      </div>
      {test.data &&
        (test.data.ok ? (
          <Banner tone="success" title="Matterbridge answers.">
            The address, token and account check out.
          </Banner>
        ) : (
          <Banner tone="error" title="Not yet.">
            {test.data.error}
          </Banner>
        ))}
      {test.isError && <Banner tone="error">{test.error.message}</Banner>}
    </>
  );
}

function MatterbridgeSecret({ ctx, id, label }: { ctx: BodyCtx; id: string; label: string }) {
  const m = ctx.view.matterbridge;
  const st = ctx.state(`matterbridge.${id}`);
  return (
    <SecretSetting
      key={m?.updated_at ?? "none"}
      label={label}
      isSet={Boolean(((m?.values?.[id] ?? {}) as { set?: boolean }).set)}
      updatedBy={m?.updated_by}
      updatedAt={m?.updated_at}
      value={st.value as string | undefined}
      onChange={(x) => st.onChange(x)}
    />
  );
}

/** The chat networks of the Matterbridge Lens runs, the rooms they became, and WhatsApp's QR code while pairing. */
function RunMatterbridge({ ctx, status }: { ctx: BodyCtx; status: MatterbridgeStatus | null }) {
  const net = "flex flex-col gap-3 rounded-sm border border-border-subtle p-3";
  const title = "text-[13px] font-bold leading-tight text-fg-strong";
  return (
    <>
      {status?.error && (
        <Banner tone="error" title="Matterbridge’s config isn’t written yet.">
          {status.error}
        </Banner>
      )}
      {status?.whatsapp_qr && (
        <Banner title="Link WhatsApp.">
          On the phone with that number, open WhatsApp › Linked devices › Link a device and scan this. It changes every
          few seconds.
          <pre
            aria-label="WhatsApp QR code"
            className="mt-2 w-fit bg-white p-2 font-mono text-[9px] leading-[1] text-black"
          >
            {status.whatsapp_qr}
          </pre>
        </Banner>
      )}
      <div className={net}>
        <span className={title}>Slack</span>
        <MatterbridgeSecret ctx={ctx} id="slack_token" label="Bot token (xoxb-…)" />
        <F ctx={ctx} id="matterbridge.slack_channels" />
      </div>
      <div className={net}>
        <span className={title}>WhatsApp</span>
        <div className="grid gap-3 sm:grid-cols-2">
          <F ctx={ctx} id="matterbridge.whatsapp_number" />
          <F ctx={ctx} id="matterbridge.whatsapp_groups" />
        </div>
      </div>
      <div className={net}>
        <span className={title}>Telegram</span>
        <MatterbridgeSecret ctx={ctx} id="telegram_token" label="Bot token (from @BotFather)" />
        <F ctx={ctx} id="matterbridge.telegram_chats" />
      </div>
      <div className={net}>
        <span className={title}>Discord</span>
        <MatterbridgeSecret ctx={ctx} id="discord_token" label="Bot token" />
        <div className="grid gap-3 sm:grid-cols-2">
          <F ctx={ctx} id="matterbridge.discord_server" />
          <F ctx={ctx} id="matterbridge.discord_channels" />
        </div>
      </div>
      <div className={net}>
        <span className={title}>Matrix</span>
        <div className="grid gap-3 sm:grid-cols-2">
          <F ctx={ctx} id="matterbridge.matrix_server" />
          <F ctx={ctx} id="matterbridge.matrix_login" />
        </div>
        <MatterbridgeSecret ctx={ctx} id="matrix_password" label="Matrix password" />
        <F ctx={ctx} id="matterbridge.matrix_rooms" />
      </div>
      {(status?.gateways ?? []).length > 0 && (
        <p className="text-[12.5px] leading-normal text-fg-secondary">
          Rooms, by the name to use in Rooms for a namespace’s assistant:{" "}
          {(status?.gateways ?? []).map((g, i) => (
            <span key={g.gateway}>
              {i > 0 && ", "}
              <code className="font-mono">{g.gateway}</code>
            </span>
          ))}
          .
        </p>
      )}
    </>
  );
}

function AiBody({ ctx }: { ctx: BodyCtx }) {
  const tools = ctx.state("ai.disabled_tools");
  const off = (tools.value as string[]) ?? [];
  const enabled = Boolean(ctx.form["ai.tools"]);
  const tts = String(ctx.form["voice.tts_provider"] ?? "openai");
  return (
    <>
      <p className="text-[13px] leading-normal text-fg-secondary">
        The assistant uses the model set in LLM provider; it needs one that supports tool calls.
      </p>
      <F ctx={ctx} id="ai.tools" />
      <F ctx={ctx} id="ai.compact" />
      <F ctx={ctx} id="ai.refine_notes" />
      <F ctx={ctx} id="ai.organise_notes" />
      <div className="flex flex-col gap-2">
        <span className="text-[13px] font-bold leading-tight text-fg-strong">Tools</span>
        <ul className="flex flex-col gap-2">
          {AI_TOOLS.map((t) => (
            <li
              key={t.name}
              className={cn("grid grid-cols-[40px_minmax(0,1fr)_auto] items-center gap-2.5", !enabled && "opacity-50")}
            >
              <Switch
                aria-label={t.label}
                checked={!off.includes(t.name)}
                disabled={!enabled}
                onCheckedChange={(on) => tools.onChange(on ? off.filter((x) => x !== t.name) : [...off, t.name])}
              />
              <span className="text-[13px] font-medium leading-[1.3]">{t.label}</span>
              <span className={cn("text-[11px] font-semibold", t.acts ? "text-gold-dark" : "text-fg-muted")}>
                {t.acts ? "needs approval" : "read"}
              </span>
            </li>
          ))}
        </ul>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <F ctx={ctx} id="ai.confirm_over_recordings" />
        <F ctx={ctx} id="ai.confirm_over_cost" />
        <F ctx={ctx} id="ai.price_in" />
        <F ctx={ctx} id="ai.price_out" />
        <F ctx={ctx} id="ai.max_steps" />
        <F ctx={ctx} id="ai.max_transcript_reads" />
      </div>
      <div className="flex flex-col gap-3">
        <span className="text-[13px] font-bold leading-tight text-fg-strong">Voice</span>
        <p className="text-[13px] leading-normal text-fg-secondary">
          The mic in chat and on the assistant home. By default this server turns speech into text with its own
          transcription engine, so it doesn’t leave the server; a speech provider is faster on small machines.
        </p>
        <div className="grid gap-3 sm:grid-cols-2">
          <F ctx={ctx} id="voice.input" />
          <F ctx={ctx} id="voice.stt" />
          <F ctx={ctx} id="voice.tts_provider" />
          <F
            ctx={ctx}
            id="voice.tts_model"
            hint={
              tts === "elevenlabs"
                ? "Empty: eleven_multilingual_v2"
                : tts === "deepgram"
                  ? "The Aura voice, e.g. aura-2-thalia-en (the default)"
                  : undefined
            }
          />
          {tts !== "deepgram" && (
            <F
              ctx={ctx}
              id="voice.tts_voice"
              hint={tts === "elevenlabs" ? "An ElevenLabs voice ID; empty: a premade voice" : undefined}
            />
          )}
          {tts === "openai" && <F ctx={ctx} id="voice.tts_base_url" />}
        </div>
        {tts === "openai" ? (
          <SecretSetting
            key={ctx.view.voice?.updated_at ?? "none"}
            label="Speech server API key"
            isSet={Boolean(((ctx.view.voice?.values?.tts_api_key ?? {}) as { set?: boolean }).set)}
            updatedBy={ctx.view.voice?.updated_by}
            updatedAt={ctx.view.voice?.updated_at}
            value={ctx.state("voice.tts_api_key").value as string | undefined}
            onChange={(x) => ctx.state("voice.tts_api_key").onChange(x)}
          />
        ) : (
          <p className="text-[12.5px] text-fg-secondary">
            Uses the key and address in <Link href="/settings/speech-providers">Speech providers</Link>.
          </p>
        )}
      </div>
      <RoutineChoices ctx={ctx} />
      <div className="flex flex-col gap-1.5">
        <span className="text-[13px] font-bold text-fg-strong">Limits per role</span>
        <div className="grid grid-cols-[80px_minmax(0,1fr)] gap-x-2.5 gap-y-1 text-[13px] leading-[1.4]">
          <b>Viewer</b>
          <span className="text-fg-secondary">The read tools only.</span>
          <b>Editor</b>
          <span className="text-fg-secondary">Also the tools that change things, each waiting for their approval.</span>
          <b>Owner</b>
          <span className="text-fg-secondary">The same as an editor, in the namespaces they own.</span>
        </div>
      </div>
    </>
  );
}

function RoutineChoices({ ctx }: { ctx: BodyCtx }) {
  const client = useApiClient();
  const d = ctx.view.decisions;
  const secret = (d?.values?.api_key ?? {}) as { set?: boolean };
  const key = ctx.state("decisions.api_key");
  const keyFromEnv = (d?.locked ?? []).includes("api_key");
  const engine = String(ctx.form["decisions.engine"] ?? "auto");
  const status = useQuery({
    queryKey: ["decision-status", d?.updated_at ?? "none"],
    queryFn: () => data(Admin.decisionStatus({ client })),
    enabled: engine === "laya",
  });
  const test = useMutation({ mutationFn: () => data(Admin.testDecisions({ client })) });
  const laya = status.data?.laya;
  return (
    <div className="flex flex-col gap-3">
      <span className="text-[13px] font-bold leading-tight text-fg-strong">Routine choices</span>
      <p className="text-[13px] leading-normal text-fg-secondary">
        Choices like which namespace a file goes in are made for you. A decision model answers them faster and for far
        less than the LLM: Jev online (get a key at typesafe.ai), or Laya on this machine for free.
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        <F ctx={ctx} id="decisions.engine" />
        <F ctx={ctx} id="decisions.act_above" />
      </div>
      {engine === "laya" ? (
        <>
          <div className="grid gap-3 sm:grid-cols-2">
            <F ctx={ctx} id="decisions.laya_model" />
            <F ctx={ctx} id="decisions.laya_url" />
          </div>
          {laya && !ctx.dirty && (
            <Banner
              tone={laya.available ? "success" : "warning"}
              title={
                laya.available
                  ? laya.where === "server"
                    ? "Laya answers from the Laya server."
                    : "Laya answers on this machine."
                  : "Laya can’t answer here yet; the LLM takes these choices until it can."
              }
            >
              {laya.available
                ? `${laya.model}. Lens fetched laya-mlx and the model itself.`
                : status.data?.apple_silicon
                  ? `${laya.reason}.`
                  : "Laya runs on MLX, which needs a Mac with Apple Silicon. On a Mac running Lens in Docker, run lens decide-server on the Mac and put its address in Laya server (http://host.docker.internal:8790/v1)."}
            </Banner>
          )}
        </>
      ) : (
        <>
          {keyFromEnv ? (
            <p className="text-[13px] text-fg-secondary">
              The decision model’s key is set by TYPESAFE_API_KEY in .env.
            </p>
          ) : (
            <SecretSetting
              key={d?.updated_at ?? "none"}
              label="Decision model API key"
              isSet={Boolean(secret.set)}
              updatedBy={d?.updated_by}
              updatedAt={d?.updated_at}
              value={key.value as string | undefined}
              onChange={(x) => key.onChange(x)}
            />
          )}
          <div className="grid gap-3 sm:grid-cols-3">
            <F ctx={ctx} id="decisions.base_url" />
            <F ctx={ctx} id="decisions.model" />
            <F ctx={ctx} id="decisions.timeout" />
          </div>
        </>
      )}
      <div className="flex flex-wrap items-center gap-2.5">
        <Button size="sm" icon={<PlugZap />} onClick={() => test.mutate()} disabled={test.isPending}>
          {test.isPending ? "Testing…" : "Test"}
        </Button>
        <span className="text-[12px] text-fg-muted">
          {ctx.dirty
            ? "Tests the saved settings, not your unsaved changes"
            : "Takes one made-up choice and says who answered"}
        </span>
      </div>
      {test.data &&
        (test.data.ok ? (
          <Banner tone="success" title={`${WHO[test.data.by ?? ""] ?? test.data.by} answered.`}>
            Chose {test.data.choice}, {Math.round((test.data.confidence ?? 0) * 100)}% sure · {test.data.ms} ms
          </Banner>
        ) : (
          <Banner tone="error" title="The test failed.">
            {test.data.error}
          </Banner>
        ))}
      {test.isError && <Banner tone="error">{test.error.message}</Banner>}
    </div>
  );
}

const WHO: Record<string, string> = { jev: "Jev", laya: "Laya", llm: "The LLM" };

function SearchBody({ ctx }: { ctx: BodyCtx }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const e = ctx.view.embeddings?.values ?? {};
  const secret = (e.api_key ?? {}) as { set?: boolean };
  const key = ctx.state("embeddings.api_key");
  const on = Boolean(ctx.form["embeddings.enabled"]);
  const search = ctx.view.search;
  const osPassword = ctx.state("search.opensearch_password");
  const status = useQuery({
    queryKey: ["semantic-status"],
    queryFn: () => data(Admin.semanticStatus({ client })),
    refetchInterval: (q) => (q.state.data && q.state.data.indexed < q.state.data.recordings ? 15_000 : false),
  });
  const test = useMutation({ mutationFn: () => data(Admin.testEmbeddings({ client })) });
  const index = useMutation({
    mutationFn: () => data(Admin.indexSemantic({ client })),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["semantic-status"] }),
  });
  const s = status.data;
  return (
    <>
      <F ctx={ctx} id="search.engine" />
      {ctx.form["search.engine"] === "opensearch" && (
        <>
          <F ctx={ctx} id="search.opensearch_url" />
          <div className="grid gap-3 sm:grid-cols-2">
            <F ctx={ctx} id="search.opensearch_user" />
            <SecretSetting
              key={search?.updated_at ?? "none"}
              label="Password"
              isSet={Boolean(((search?.values?.opensearch_password ?? {}) as { set?: boolean }).set)}
              updatedBy={search?.updated_by}
              updatedAt={search?.updated_at}
              value={osPassword.value as string | undefined}
              onChange={(x) => osPassword.onChange(x)}
            />
          </div>
          <F ctx={ctx} id="search.opensearch_verify" />
        </>
      )}
      <F ctx={ctx} id="search.stemming" />
      <Reindex />
      <h3 className="m-0 mt-3 text-[15px] font-bold text-fg">Search by meaning</h3>
      <p className="m-0 text-[13px] leading-normal text-fg-secondary">
        Passages of transcripts, pages and descriptions are embedded by an OpenAI-compatible server (Ollama, llama.cpp,
        vLLM, LM Studio, OpenAI), so a search also finds moments about the same thing in other words. A local model
        keeps everything on your machine.
      </p>
      <F ctx={ctx} id="embeddings.enabled" />
      {on && (
        <>
          <div className="grid gap-3 sm:grid-cols-2">
            <F ctx={ctx} id="embeddings.base_url" />
            <F ctx={ctx} id="embeddings.model" />
          </div>
          <SecretSetting
            key={ctx.view.embeddings?.updated_at ?? "none"}
            label="API key"
            isSet={Boolean(secret.set)}
            updatedBy={ctx.view.embeddings?.updated_by}
            updatedAt={ctx.view.embeddings?.updated_at}
            value={key.value as string | undefined}
            onChange={(x) => key.onChange(x)}
          />
          <F ctx={ctx} id="embeddings.api_key_env" hint="Used only when no key is stored above" />
          <div className="grid gap-3 sm:grid-cols-2">
            <F ctx={ctx} id="embeddings.min_similarity" />
            <F ctx={ctx} id="embeddings.neighbours" />
            <F ctx={ctx} id="embeddings.passage_chars" />
            <F ctx={ctx} id="embeddings.batch_size" />
            <F ctx={ctx} id="embeddings.query_prefix" />
            <F ctx={ctx} id="embeddings.document_prefix" />
            <F ctx={ctx} id="embeddings.timeout" />
          </div>
          <div className="flex flex-wrap items-center gap-2.5">
            <Button size="sm" icon={<PlugZap />} onClick={() => test.mutate()} disabled={test.isPending}>
              {test.isPending ? "Testing…" : "Test"}
            </Button>
            <span className="text-[12px] text-fg-muted">
              {ctx.dirty
                ? "Tests the saved settings, not your unsaved changes"
                : "Embeds one sentence and reports the vector size and latency"}
            </span>
          </div>
          {test.data &&
            (test.data.ok ? (
              <Banner tone="success" title="The model answered.">
                {test.data.model ?? "Model"} · {test.data.dimension} dimensions · {test.data.ms} ms
              </Banner>
            ) : (
              <Banner tone="error" title="The test failed.">
                {test.data.error}
              </Banner>
            ))}
          {test.isError && <Banner tone="error">{test.error.message}</Banner>}
        </>
      )}
      {s && (
        <div className="flex flex-col gap-2 rounded-md border border-blue-border bg-blue-surface px-3.5 py-3">
          <div className="flex flex-wrap items-center gap-2 text-[13px] font-semibold">
            <span className="flex-1">
              {!s.configured
                ? "Search by meaning is off"
                : s.current
                  ? `${s.indexed} of ${s.recordings} recordings indexed with ${s.model}`
                  : s.indexed_model
                    ? `Indexed with ${s.indexed_model}; nothing yet with ${s.model}`
                    : `Nothing indexed with ${s.model} yet`}
            </span>
            {s.configured && s.indexed < s.recordings && (
              <Button
                size="xs"
                icon={<RefreshCw />}
                onClick={() => index.mutate()}
                disabled={index.isPending || ctx.dirty}
                disabledReason={ctx.dirty ? "Save your changes first" : undefined}
              >
                {index.isPending ? "Queuing…" : "Index now"}
              </Button>
            )}
          </div>
          <span className="text-[12px] leading-[1.4] text-fg-secondary">
            {s.configured
              ? `${s.passages} passages${s.dimension ? ` of ${s.dimension} dimensions` : ""}. New recordings are indexed by their pipeline's Index for meaning step; the “${"Index for search by meaning"}” routine catches up every hour (Routines).`
              : "Turn it on and name a model to start. Recordings are indexed by their pipelines and an hourly routine."}
            {index.data &&
              ` Queued ${index.data.recordings}${index.data.remaining ? "; more are waiting for the next run" : ""}.`}
          </span>
          {index.isError && <span className="text-[12px] text-red-dark">{index.error.message}</span>}
        </div>
      )}
    </>
  );
}

function Reindex() {
  const client = useApiClient();
  const run = useMutation({
    mutationFn: () => data(Admin.reindexSearch({ client })),
  });
  return (
    <div className="flex flex-col gap-2 rounded-md border border-blue-border bg-blue-surface px-3.5 py-3">
      <div className="flex flex-wrap items-center gap-2 text-[13px] font-semibold">
        <span className="flex-1">{run.isSuccess ? "Reindexing in the background" : "Rebuild the search index"}</span>
        <Button size="xs" icon={<RefreshCw />} onClick={() => run.mutate()} disabled={run.isPending || run.isSuccess}>
          {run.isPending ? "Starting…" : run.isSuccess ? "Started" : "Reindex now"}
        </Button>
      </div>
      <span className="text-[12px] leading-[1.4] text-fg-secondary">
        Search keeps using the old index until this finishes.{" "}
        {run.isSuccess
          ? "Progress isn’t reported yet; searches pick up the new index when it’s done."
          : "Run it after changing word search or stemming."}
      </span>
      {run.isError && <span className="text-[12px] text-red-dark">{run.error.message}</span>}
    </div>
  );
}

function IiifBody({ ctx }: { ctx: BodyCtx }) {
  const client = useApiClient();
  const { namespaces } = useArchive();
  const viewers = (ctx.view.iiif?.values?.viewers as { name?: string; url?: string }[] | undefined) ?? [];
  const profiles = useQueries({
    queries: namespaces.map((n) => ({
      queryKey: ["namespace-metadata", n.name],
      queryFn: async () =>
        (await data(Metadata.getNamespaceMetadata({ client, path: { name: n.name } }))) as unknown as {
          profile: { default_access?: string };
        },
      staleTime: 60_000,
    })),
  });
  const ready = profiles.length > 0 && profiles.every((q) => q.data);
  const inited = useRef(false);
  useEffect(() => {
    if (!ready || inited.current) return;
    inited.current = true;
    ctx.initNsAccess(
      Object.fromEntries(namespaces.map((n, i) => [n.name, profiles[i].data?.profile.default_access ?? "private"])),
    );
  }, [ready]);

  return (
    <>
      <F ctx={ctx} id="iiif.base_url" />
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <span className="text-[13px] font-bold leading-tight text-fg-strong">Default IIIF version</span>
          <Select
            aria-label="Default IIIF version"
            value="3"
            onChange={() => undefined}
            options={[
              { value: "3", label: "Presentation 3.0" },
              {
                value: "4",
                label: "Presentation 4.0 (RC) — not available yet",
                disabled: true,
              },
            ]}
          />
        </div>
        <F
          ctx={ctx}
          id="iiif.rights"
          options={[
            { value: "", label: "None" },
            ...RIGHTS.map((r) => ({
              value: r.uri,
              label: `${r.code} — ${r.name}`,
            })),
          ]}
        />
        <F ctx={ctx} id="iiif.attribution" />
        <F ctx={ctx} id="iiif.default_language" />
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <F ctx={ctx} id="iiif.provider.name" />
        <F ctx={ctx} id="iiif.provider.homepage" label="Homepage" />
        <F ctx={ctx} id="iiif.provider.logo" label="Logo" />
      </div>
      <F
        ctx={ctx}
        id="iiif.layers"
        hint="Annotation layers are published only when a recording’s transcript is open."
      />
      <div className="flex flex-col gap-1.5">
        <span className="text-[13px] font-bold leading-tight text-fg-strong">
          External viewers <span className="font-normal text-fg-muted">· “Open in” buttons</span>
        </span>
        {viewers.length ? (
          <ul className="flex flex-col gap-1.5">
            {viewers.map((x, i) => (
              <li key={i} className="grid grid-cols-[130px_minmax(0,1fr)] items-center gap-2.5 text-[12.5px]">
                <b className="font-semibold">{x.name ?? "Viewer"}</b>
                <code className="truncate font-mono text-[11.5px] text-fg-secondary">{x.url}</code>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-[12.5px] text-fg-muted">None yet.</p>
        )}
        <div className="flex items-center gap-2">
          <Button
            size="xs"
            disabled
            disabledReason="The server can’t save viewer links from the app yet; set iiif.viewers in archive.yaml ({manifest} and {content_state} placeholders)"
          >
            Add viewer
          </Button>
          <span className="text-[12px] text-fg-muted">Set in the server’s archive.yaml for now.</span>
        </div>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <F ctx={ctx} id="iiif.allowed_origins" />
        <F ctx={ctx} id="iiif.token_minutes" />
      </div>
      <div className="flex flex-col gap-2">
        <span className="text-[13px] font-bold leading-tight text-fg-strong">Access per namespace</span>
        <p className="text-[12.5px] text-fg-muted">
          The default for recordings that don’t set their own. Saved into each namespace’s metadata profile.
        </p>
        {!ready ? (
          <Skeleton className="h-24 w-full" />
        ) : (
          <ul className="flex flex-col gap-2">
            {namespaces.map((n) => {
              const val = ctx.nsAccess[n.name] ?? "private";
              return (
                <li
                  key={n.name}
                  className="grid items-center gap-2.5 text-[13px] font-medium sm:grid-cols-[170px_170px_minmax(0,1fr)]"
                >
                  <span>{n.name}</span>
                  <Select
                    aria-label={`Default access for ${n.name}`}
                    size="sm"
                    value={val}
                    onChange={(e) => ctx.setNsAccess(n.name, e.target.value)}
                    options={ACCESS.map((a) => ({
                      value: a.value,
                      label: a.label,
                    }))}
                  />
                  <span className="text-[12.5px] font-normal leading-[1.3] text-fg-secondary">
                    {ACCESS.find((a) => a.value === val)?.hint}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </div>
      <Link href="/iiif" className="self-start text-[13px] font-semibold text-fg-accent hover:underline">
        Metadata profiles ({namespaces.length}) →
      </Link>
    </>
  );
}

/** Which programs this server makes PDFs with (set at startup): what it can read without them, and with them. */
function Converters({ view }: { view: SettingsView }) {
  const b = view.bootstrap ?? {};
  const missing = (v?: string) => !v || v === "not installed";
  const rows: [string, string | undefined, string][] = [
    ["LibreOffice", b.soffice, "Word, PowerPoint and spreadsheet files, OpenDocument and RTF"],
    ["Chromium", b.chromium, "text, Markdown, saved web pages and emails (LibreOffice does them too, plainer)"],
    ["anytopdf", b.anytopdf, "text, web pages, emails and photographed pages; downloaded when chosen above"],
  ];
  return (
    <div className="flex flex-col gap-1.5 rounded-md border border-border bg-surface p-3.5">
      <b className="text-[13.5px] font-bold text-fg">Making PDFs</b>
      <ul className="flex flex-col gap-1 text-[13px] leading-[1.45] text-fg-secondary">
        {rows.map(([name, path, what]) => (
          <li key={name}>
            <b className="font-semibold text-fg">{name}</b>:{" "}
            {missing(path) ? "not installed" : <code className="font-mono text-[12px]">{path}</code>} · {what}
          </li>
        ))}
      </ul>
      <p className="text-[12.5px] leading-[1.45] text-fg-muted">
        PDFs and images are read without either. The lens:full image has both; their paths are set at startup
        (documents.soffice, documents.chromium).
      </p>
    </div>
  );
}

function Startup({ view }: { view: SettingsView }) {
  const b = view.bootstrap ?? {};
  const rows: [string, ReactNode][] = [
    ["Database", b.database ?? "—"],
    ["Data folder", b.data_dir ?? "—"],
    [
      "Encryption key",
      b.secret_key === "ARCHIVE_SECRET_KEY"
        ? "from env ARCHIVE_SECRET_KEY · set"
        : "a key file in the data folder (secret.key)",
    ],
    ["rclone", b.rclone ?? "—"],
    ["LibreOffice", b.soffice ?? "—"],
    ["Chromium", b.chromium ?? "—"],
    [
      "Web capture",
      b.web_networks?.length ? `public addresses and ${b.web_networks.join(" · ")}` : "public addresses only",
    ],
    ["YOLOX model", b.yolox_model ?? "—"],
    ["Watchable folders", b.local_roots?.length ? b.local_roots.join(" · ") : "none: local folders can’t be watched"],
  ];
  return (
    <div className="flex flex-col gap-3 rounded-md border border-dashed border-border bg-surface p-5">
      <div className="flex items-center gap-2">
        <Terminal aria-hidden className="size-4 text-fg-secondary" />
        <b className="flex-1 text-[17px] font-bold">Set at startup</b>
        <span className="rounded-pill border border-border bg-background px-[7px] text-[10.5px] font-semibold leading-[18px] text-fg-secondary">
          Read-only
        </span>
      </div>
      <p className="text-[13px] leading-normal text-fg-secondary">
        These come from the server’s config file or environment and can only be changed there, followed by a restart.
      </p>
      <dl>
        {rows.map(([k, val]) => (
          <div
            key={k}
            className="grid gap-1 border-t border-border py-2 text-[13px] leading-[1.45] sm:grid-cols-[150px_minmax(0,1fr)] sm:gap-2.5"
          >
            <dt className="text-fg-secondary">{k}</dt>
            <dd>
              <code className="break-all font-mono text-[12.5px] leading-normal">{val}</code>
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function SpeechProvidersBody({ ctx }: { ctx: BodyCtx }) {
  const view = ctx.view.speech;
  return (
    <>
      <p className="m-0 text-[13px] leading-normal text-fg-secondary">
        Nothing is sent to these services until you pick one in Transcription, Speaker separation or the AI assistant’s
        Voice. Keys are stored encrypted. Change an address to use a proxy, an EU region or a compatible server.
      </p>
      {SPEECH_PROVIDERS.map((p) => (
        <div key={p.id} className="flex flex-col gap-3 border-t border-border pt-4">
          <Sub>{p.label}</Sub>
          <p className="m-0 text-[12.5px] leading-[1.45] text-fg-secondary">{p.about}</p>
          <div className="grid gap-3 sm:grid-cols-2">
            <F ctx={ctx} id={`speech.${p.id}_base_url`} />
            <F ctx={ctx} id={`speech.${p.id}_model`} />
          </div>
          <SecretSetting
            key={view?.updated_at ?? "none"}
            label={`${p.label} API key`}
            isSet={Boolean(((view?.values?.[`${p.id}_api_key`] ?? {}) as { set?: boolean }).set)}
            updatedBy={view?.updated_by}
            updatedAt={view?.updated_at}
            value={ctx.state(`speech.${p.id}_api_key`).value as string | undefined}
            onChange={(x) => ctx.state(`speech.${p.id}_api_key`).onChange(x)}
          />
          <SpeechTest provider={p.id} dirty={ctx.dirty} />
        </div>
      ))}
      <div className="grid gap-3 border-t border-border pt-4 sm:grid-cols-2">
        <F ctx={ctx} id="speech.sentiment" />
        <F ctx={ctx} id="speech.timeout" />
      </div>
    </>
  );
}

function SpeechTest({ provider, dirty }: { provider: string; dirty: boolean }) {
  const client = useApiClient();
  const test = useMutation({ mutationFn: () => data(Admin.testSpeech({ client, query: { provider } })) });
  return (
    <>
      <div className="flex flex-wrap items-center gap-2.5">
        <Button size="sm" icon={<PlugZap />} onClick={() => test.mutate()} disabled={test.isPending}>
          {test.isPending ? "Testing…" : "Test"}
        </Button>
        <span className="text-[12px] text-fg-muted">
          {dirty
            ? "Tests the saved settings, not your unsaved changes"
            : "Checks the address and key; nothing is billed"}
        </span>
      </div>
      {test.data &&
        (test.data.ok ? (
          <Banner tone="success" title="It answered.">
            {test.data.detail} · {test.data.ms} ms
          </Banner>
        ) : (
          <Banner tone="error" title="The test failed.">
            {test.data.error}
          </Banner>
        ))}
      {test.isError && <Banner tone="error">{test.error.message}</Banner>}
    </>
  );
}

const PHASE: Record<string, string> = {
  off: "Off",
  "fetching-server": "Fetching llama.cpp",
  downloading: "Downloading the model",
  starting: "Starting",
  running: "Running",
  error: "Not running",
};

function gb(n: number) {
  return `${n < 10 ? n.toFixed(1) : Math.round(n)} GB`;
}

function LocalModelBody({ ctx }: { ctx: BodyCtx }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const status = useQuery({
    queryKey: ["local-llm-status"],
    queryFn: () => data(Admin.localLlmStatus({ client })),
    refetchInterval: (q) => {
      const p = q.state.data?.phase;
      return p && p !== "off" && p !== "running" ? 2_000 : 15_000;
    },
  });
  const remove = useMutation({
    mutationFn: (model: string) => data(Admin.removeLocalModel({ client, query: { model } })),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["local-llm-status"] }),
  });
  const pick = ctx.state("local_llm.model");
  const picked = String(ctx.form["local_llm.model"] ?? "");
  const s = status.data;
  const mach = (s?.machine ?? {}) as { memory_gb?: number | null; disk_free_gb?: number | null; cpus?: number };
  const pct = s?.progress?.total ? Math.round((s.progress.done / s.progress.total) * 100) : null;
  return (
    <>
      {s && s.enabled && (
        <Banner
          tone={s.phase === "running" ? "success" : s.phase === "error" ? "error" : "info"}
          title={`${PHASE[s.phase] ?? s.phase}${s.model ? `: ${s.model}` : ""}${pct != null ? ` · ${pct}%` : ""}`}
        >
          {s.phase === "running" ? `Answering at ${s.url}` : (s.error ?? s.log?.at(-1) ?? "Getting it ready")}
        </Banner>
      )}
      <F ctx={ctx} id="local_llm.enabled" />
      <p className="m-0 text-[12.5px] leading-[1.45] text-fg-secondary">
        This machine: {mach.memory_gb != null ? `${gb(mach.memory_gb)} memory` : "memory unknown"}
        {mach.disk_free_gb != null ? `, ${gb(mach.disk_free_gb)} free disk` : ""}
        {mach.cpus ? `, ${mach.cpus} CPUs` : ""}. Models that don’t fit are greyed out; small ones run on a Raspberry
        Pi.
      </p>
      {!s && <Skeleton className="h-40" />}
      {s && (
        <ul className="m-0 flex list-none flex-col gap-1.5 p-0" aria-label="Models">
          {s.catalog.map((m) => (
            <li key={m.id}>
              <button
                type="button"
                aria-pressed={picked === m.id}
                disabled={!m.fits || !m.room}
                onClick={() => pick.onChange(m.id)}
                className={cn(
                  "grid w-full grid-cols-[minmax(0,1fr)_auto] gap-x-3 rounded-md border px-3 py-2 text-left",
                  picked === m.id ? "border-blue bg-blue-surface" : "border-border",
                  (!m.fits || !m.room) && "opacity-50",
                )}
              >
                <span className="text-[13px] font-semibold">
                  {m.label}
                  {m.downloaded && <span className="ml-2 text-[11px] font-semibold text-fg-muted">downloaded</span>}
                </span>
                <span className="text-[12px] text-fg-secondary">
                  {gb(m.size_gb)} · needs {gb(m.memory_gb)}
                </span>
                <span className="text-[12px] text-fg-secondary">
                  {m.about}
                  {m.tools ? "" : " No tool calls."}
                </span>
                <span className="text-[11px] text-fg-muted">
                  {!m.fits ? "too big here" : !m.room ? "no room on disk" : (m.license ?? "")}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      <F ctx={ctx} id="local_llm.model" label="Model (or any GGUF on Hugging Face)" />
      <F ctx={ctx} id="local_llm.use_as_provider" />
      <div className="grid gap-3 sm:grid-cols-2">
        <F ctx={ctx} id="local_llm.context" />
        <F ctx={ctx} id="local_llm.threads" />
        <F ctx={ctx} id="local_llm.gpu_layers" />
        <F ctx={ctx} id="local_llm.port" />
        <F ctx={ctx} id="local_llm.host" />
      </div>
      {s && s.files.length > 0 && (
        <div className="flex flex-col gap-1.5 border-t border-border pt-3">
          <Sub>Downloaded</Sub>
          {s.files.map((f) => {
            const m = s.catalog.find((x) => f.path.endsWith(`/${x.file}`));
            const ref = m ? m.id : `hf:${f.path.replace("__", "/")}`;
            return (
              <div key={f.path} className="flex items-center gap-2.5 text-[13px]">
                <span className="flex-1 font-mono text-[12px]">{f.path}</span>
                <span className="text-fg-muted">{gb(f.size_gb)}</span>
                <Button size="sm" onClick={() => remove.mutate(ref)} disabled={remove.isPending}>
                  Delete
                </Button>
              </div>
            );
          })}
          {remove.isError && <Banner tone="error">{remove.error.message}</Banner>}
        </div>
      )}
    </>
  );
}
