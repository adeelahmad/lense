"use client";

import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { PlugZap, RefreshCw, Terminal } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { Admin, Fedora, Mesh, Metadata, Sensors, Shares, Sources, Users } from "@/app/openapi-client";
import type { HttpsStatus, MatterbridgeStatus } from "@/app/openapi-client/types.gen";
import { MonitoringSwitch } from "@/components/admin/monitoring-switch";
import { useServices } from "@/components/admin/service-switch";
import { BackupsPanel } from "@/components/settings/backups-panel";
import { ComponentsStatus } from "@/components/settings/components-status";
import { NodesBody } from "@/components/settings/mesh-nodes";
import { EditionChoice, NetworkReach } from "@/components/settings/install-settings";
import { LogViewer } from "@/components/settings/log-viewer";
import { McpServers } from "@/components/settings/mcp-servers";
import { AllTokens } from "@/components/account/all-tokens";
import { ACCESS } from "@/components/iiif/metadata-model";
import { RIGHTS } from "@/components/iiif/rights";
import { hubText } from "@/components/sensors/sensor-model";
import { SecretSetting, SettingField, ZoneBar, type FieldState } from "@/components/settings/fields";
import {
  KeyLink,
  ModelProviderPicker,
  providerAt,
  useModelProviders,
  type ModelProvider,
} from "@/components/settings/model-providers";
import { SandboxGatewaySwitch } from "@/components/settings/sandbox-gateway";
import { ScalingGroups } from "@/components/settings/scaling-panel";
import { SignInProviders } from "@/components/settings/sign-in-providers";
import { UpdatesPanel } from "@/components/settings/updates-panel";
import { settingHref } from "@/components/settings/find";
import { ReferenceStatus } from "@/components/settings/vault-picker";
import { VoicePicker } from "@/components/settings/voice-picker";
import { AddConnection } from "@/components/storage/add-connection";
import { NOT_STORAGE, fileStoreTry } from "@/components/storage/file-store";
import {
  AI_TOOLS,
  BACKUP_SCHEDULES,
  FIELDS,
  SECTIONS,
  SPEECH_PROVIDERS,
  UPLOAD_TYPES,
  type FieldSpec,
  type SectionId,
  type SettingsView,
} from "@/components/settings/model";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Field, Input, Select, Switch, Textarea } from "@/components/ui/field";
import { CodeBlock, Skeleton } from "@/components/ui/states";
import { data, useApiClient } from "@/lib/api/browser";
import { relative, shortDate } from "@/lib/format";
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
  suggestions,
}: {
  ctx: BodyCtx;
  id: string;
  label?: ReactNode;
  hint?: ReactNode;
  options?: FieldSpec["options"];
  className?: string;
  suggestions?: string[];
}) {
  const spec = ctx.spec(id);
  return (
    <SettingField
      spec={options ? { ...spec, options } : spec}
      state={ctx.state(id)}
      label={label}
      hint={hint}
      className={className}
      suggestions={suggestions}
    />
  );
}

/** Options most people leave as they are, folded under a line that says what's in them. It opens by itself when one of
 * `ids` has an error, so a save that fails shows the field; Find and ⌘K open it on the field they found. */
function More({
  ctx,
  ids,
  title,
  open,
  children,
}: {
  ctx: BodyCtx;
  ids: string[];
  title: ReactNode;
  /** Open to begin with (a provider already in use). */
  open?: boolean;
  children: ReactNode;
}) {
  const failed = ids.some((id) => Boolean(ctx.state(id).error));
  return (
    <details open={failed || open || undefined} className="group text-[13px]">
      <summary className="cursor-pointer font-semibold text-fg-secondary hover:text-fg">{title}</summary>
      <div className="mt-3 flex flex-col gap-4">{children}</div>
    </details>
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
            <F ctx={ctx} id="transcribe.vocabulary" />
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
          <F ctx={ctx} id="transcribe.vocabulary" />
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
              When valid, the three zones read: <b>new person</b> below review · <b>◆ review queue</b> between ·{" "}
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
          <More ctx={ctx} ids={["speakers.embedder", "speakers.model"]} title="Voice model">
            <div className="grid gap-3 sm:grid-cols-2">
              <F ctx={ctx} id="speakers.embedder" />
              <F ctx={ctx} id="speakers.model" />
            </div>
          </More>
        </>
      );
    case "analysis":
      return (
        <>
          <F ctx={ctx} id="analysis.cleanup" />
          {raw("analysis.cleanup") === true && (
            <div className="grid gap-3.5 sm:grid-cols-2">
              <F ctx={ctx} id="analysis.cleanup_fillers" />
              <F ctx={ctx} id="analysis.cleanup_punctuation" />
              <F ctx={ctx} id="analysis.cleanup_grammar_url" />
            </div>
          )}
          <F ctx={ctx} id="analysis.entities" />
          {raw("analysis.entities") === "spacy" && <F ctx={ctx} id="analysis.spacy_model" />}
          <F ctx={ctx} id="analysis.typing" />
          <F ctx={ctx} id="analysis.anytopdf" />
          <F ctx={ctx} id="analysis.dates" />
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
          <More
            ctx={ctx}
            ids={["video.sample_seconds", "video.scene_threshold", "video.min_shot_seconds", "video.frame_width"]}
            title="How shots are cut and keyframes kept"
          >
            <div className="grid gap-3 sm:grid-cols-2">
              <F ctx={ctx} id="video.sample_seconds" />
              <F ctx={ctx} id="video.scene_threshold" />
              <F ctx={ctx} id="video.min_shot_seconds" />
              <F ctx={ctx} id="video.frame_width" />
            </div>
          </More>
          <Sub>Text on screen</Sub>
          <F ctx={ctx} id="video.ocr_engine" />
          <More ctx={ctx} ids={["video.ocr_languages", "video.ocr_min_confidence"]} title="Languages and confidence">
            <div className="grid gap-3 sm:grid-cols-2">
              <F ctx={ctx} id="video.ocr_languages" />
              <F ctx={ctx} id="video.ocr_min_confidence" />
            </div>
          </More>
          <Sub>Faces</Sub>
          <F ctx={ctx} id="video.face_engine" />
          <More
            ctx={ctx}
            ids={["video.face_match_threshold", "video.face_review_threshold", "video.face_cluster_threshold"]}
            title="When a face matches someone"
          >
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
          </More>
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
    case "sandbox":
      return (
        <>
          <SandboxGatewaySwitch />
          <F ctx={ctx} id="sandbox.enabled" />
          <div className="grid gap-3 sm:grid-cols-2">
            <F ctx={ctx} id="sandbox.who" />
            <F ctx={ctx} id="sandbox.network" />
          </div>
          {ctx.values["sandbox.network"] === "hosts" && <F ctx={ctx} id="sandbox.hosts" />}
          <F ctx={ctx} id="sandbox.gateway" />
          <More
            ctx={ctx}
            ids={[
              "sandbox.image",
              "sandbox.cpu",
              "sandbox.memory",
              "sandbox.seconds",
              "sandbox.idle_minutes",
              "sandbox.keep_hours",
            ]}
            title="Image, CPU and memory, run time, and how long sandboxes are kept"
          >
            <F ctx={ctx} id="sandbox.image" />
            <div className="grid gap-3 sm:grid-cols-3">
              <F ctx={ctx} id="sandbox.cpu" />
              <F ctx={ctx} id="sandbox.memory" />
              <F ctx={ctx} id="sandbox.seconds" />
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <F ctx={ctx} id="sandbox.idle_minutes" />
              <F ctx={ctx} id="sandbox.keep_hours" />
            </div>
          </More>
          <p className="text-[12.5px] leading-[1.45] text-fg-muted">
            Each conversation gets a sandbox of its own the first time the assistant runs code in it, and keeps its
            files between runs. Nothing of Lens’s is inside: the assistant copies in files people attached when it needs
            them, and a file it makes reaches a namespace only once the person approves. The sandbox runs on an NVIDIA
            OpenShell gateway: Lens runs one next to itself on a Linux machine with Docker 28 or newer and a kernel with
            Landlock (6.2 or newer), or point it at one you run elsewhere.
          </p>
        </>
      );
    case "nsfw":
      return (
        <>
          <F ctx={ctx} id="nsfw.enabled" />
          <div className="grid gap-3 sm:grid-cols-3">
            <F ctx={ctx} id="nsfw.threshold" />
            <F ctx={ctx} id="nsfw.display" />
            <F ctx={ctx} id="nsfw.max_frames" />
          </div>
          <p className="text-[12.5px] leading-[1.45] text-fg-muted">
            The model is AdamCodd/vit-base-nsfw-detector (Apache-2.0), run with ONNX Runtime on this server: pictures
            aren’t sent anywhere. It’s strict, so editors can mark a resource NSFW or not by hand. A namespace’s owner
            can turn it on or off, and set its threshold and display, on{" "}
            <Link href="/admin/namespaces" className="font-semibold text-fg-accent hover:underline">
              the namespace’s page
            </Link>
            . Blurring and hiding are display preferences, not access control.
          </p>
        </>
      );
    case "enhance":
      return (
        <>
          <F ctx={ctx} id="enhance.images" />
          <div className="grid gap-3 sm:grid-cols-2">
            <F ctx={ctx} id="enhance.image_engine" />
            <F ctx={ctx} id="enhance.only_faces" />
          </div>
          <div className="grid gap-3 sm:grid-cols-4">
            <F ctx={ctx} id="enhance.min_side" />
            <F ctx={ctx} id="enhance.min_sharpness" />
            <F ctx={ctx} id="enhance.min_face_px" />
            <F ctx={ctx} id="enhance.max_scale" />
          </div>
          <p className="text-[12.5px] leading-[1.45] text-fg-muted">
            The enhanced copy is kept beside the original, encrypted like it, and is what OCR and descriptions read.
            Real-ESRGAN general x4v3 (BSD-3-Clause) runs with ONNX Runtime on this server: pictures aren’t sent
            anywhere. Any image can be enhanced on request from its Details tab, even with this off. Audio isn’t
            enhanced yet.
          </p>
        </>
      );
    case "workers":
      return (
        <>
          <F ctx={ctx} id="workers.inline" />
          <More
            ctx={ctx}
            ids={["workers.poll_seconds", "workers.stale_minutes", "workers.max_attempts", "workers.steps"]}
            title="The job queue, and the steps they run"
          >
            <div className="grid gap-3 sm:grid-cols-3">
              <F ctx={ctx} id="workers.poll_seconds" />
              <F ctx={ctx} id="workers.stale_minutes" />
              <F ctx={ctx} id="workers.max_attempts" />
            </div>
            <F ctx={ctx} id="workers.steps" />
          </More>
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
    case "telegram":
      return <TelegramBody ctx={ctx} />;
    case "slack":
      return <SlackBody ctx={ctx} />;
    case "mailbox":
      return <MailboxBody ctx={ctx} />;
    case "remote-access":
      return <TunnelBody ctx={ctx} />;
    case "https":
      return <HttpsBody ctx={ctx} />;
    case "nodes":
      return <NodesBody />;
    case "network":
      return <NetworkBody ctx={ctx} />;
    case "components":
      return (
        <>
          <EditionChoice />
          <F ctx={ctx} id="components.auto" />
          <F ctx={ctx} id="components.hf_token" />
          <ComponentsStatus ctx={ctx} />
        </>
      );
    case "access":
      return (
        <>
          <NetworkReach />
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
            People add passkeys in their profile. Someone new, or who lost their passkey, gets a sign-in link from Admin
            → Members (or <code className="font-mono text-[12px]">lens users link their@email</code> on the server).
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
    case "automations":
      return (
        <>
          <F ctx={ctx} id="automations.enabled" />
          <div className="grid gap-3 sm:grid-cols-2">
            <F ctx={ctx} id="automations.max_depth" />
            <F ctx={ctx} id="automations.max_runs_per_minute" />
            <F ctx={ctx} id="automations.keep_runs_days" />
          </div>
          <Sub>The stream of activities</Sub>
          <div className="grid gap-3 sm:grid-cols-2">
            <F ctx={ctx} id="events.poll_seconds" />
            <F ctx={ctx} id="events.keep_days" />
            <F ctx={ctx} id="automations.approval_days" />
            <F ctx={ctx} id="automations.catch_up" />
          </div>
          <p className="text-[12.5px] leading-[1.45] text-fg-secondary">
            Make and manage automations on the{" "}
            <Link href="/automations" className="font-semibold text-fg-accent hover:underline">
              Automations
            </Link>{" "}
            page. Agents read the same stream with the timeline tool (MCP and the assistant) or GET /api/v1/activities,
            and manage automations over /api/v1/automations.
          </p>
        </>
      );
    case "scaling":
      return (
        <>
          <F ctx={ctx} id="scaling.enabled" />
          <Sub>Groups</Sub>
          <ScalingGroups />
          <More
            ctx={ctx}
            ids={["scaling.tick_seconds", "scaling.join_minutes", "scaling.accrue_minutes", "scaling.tag"]}
            title="How often it looks, joining and counting costs"
          >
            <div className="grid gap-3 sm:grid-cols-2">
              <F ctx={ctx} id="scaling.tick_seconds" />
              <F ctx={ctx} id="scaling.join_minutes" />
              <F ctx={ctx} id="scaling.accrue_minutes" />
              <F ctx={ctx} id="scaling.tag" />
            </div>
          </More>
          <p className="text-[12.5px] leading-[1.45] text-fg-secondary">
            A group launches nothing until you approve its terms here; agents (the assistant, MCP and API keys) make and
            manage groups but can’t approve them. Costs are estimates from list prices, counted against each group’s
            budget. Today groups run more workers on this machine; cloud providers are planned.
          </p>
        </>
      );
    case "encryption":
      return <EncryptionBody ctx={ctx} />;
    case "secrets":
      return <SecretsBody ctx={ctx} />;
    case "backups":
      return <BackupsBody ctx={ctx} />;
    case "storage":
      return <StorageBody ctx={ctx} />;
    case "telemetry":
      return <TelemetryBody ctx={ctx} />;
    case "analytics":
      return <AnalyticsBody ctx={ctx} />;
    case "updates":
      return (
        <>
          <UpdatesPanel />
          <Sub>Checking</Sub>
          <div className="grid gap-3 sm:grid-cols-2">
            <F ctx={ctx} id="updates.check" />
            {/* shown once the server has the setting (updates.email, from the release notices) */}
            {ctx.view.updates?.values && "email" in ctx.view.updates.values && <F ctx={ctx} id="updates.email" />}
            <F ctx={ctx} id="updates.auto_install" />
          </div>
          <F ctx={ctx} id="updates.check_hours" />
        </>
      );
    case "logs":
      return <LogsBody ctx={ctx} />;
    case "fedora":
      return <FedoraBody ctx={ctx} />;
    case "sensors":
      return <SensorsBody ctx={ctx} />;
    case "nfs":
      return <NfsBody ctx={ctx} />;
    case "smb":
      return <SmbBody ctx={ctx} />;
    case "shares":
      return <SharesBody ctx={ctx} />;
    case "uploads":
      return (
        <>
          <F ctx={ctx} id="uploads.max_mb" />
          <More
            ctx={ctx}
            ids={["uploads.chunk_mb", "uploads.expire_hours", "uploads.extensions"]}
            title={`File types (${((raw("uploads.extensions") as string[] | undefined) ?? []).length} of ${UPLOAD_TYPES.length}), pieces and unfinished uploads`}
          >
            <div className="grid gap-3 sm:grid-cols-2">
              <F ctx={ctx} id="uploads.chunk_mb" />
              <F ctx={ctx} id="uploads.expire_hours" />
            </div>
            <F ctx={ctx} id="uploads.extensions" />
          </More>
          <p className="text-[12.5px] leading-[1.45] text-fg-secondary">
            Uploads arrive in pieces and carry on where they stopped if the connection drops. Finished files are kept in
            the server’s data folder, under uploads. Text and document files have their own limit, under Access &amp;
            embedding; watched folders have no limit.
          </p>
        </>
      );
    case "documents":
      return (
        <>
          <More
            ctx={ctx}
            ids={[
              "documents.page_pixels",
              "documents.thumb_pixels",
              "documents.ocr_below_chars",
              "documents.max_pages",
              "documents.convert_seconds",
            ]}
            title="Page sizes, OCR and limits"
          >
            <div className="grid gap-3 sm:grid-cols-2">
              <F ctx={ctx} id="documents.page_pixels" />
              <F ctx={ctx} id="documents.thumb_pixels" />
              <F ctx={ctx} id="documents.ocr_below_chars" />
              <F ctx={ctx} id="documents.max_pages" />
            </div>
            <F ctx={ctx} id="documents.convert_seconds" />
          </More>
          <F ctx={ctx} id="documents.attachment_resources" />
          <F ctx={ctx} id="documents.video_urls" />
          <F ctx={ctx} id="documents.web_networks" />
          <F ctx={ctx} id="documents.converter" />
          <F ctx={ctx} id="documents.anytopdf_url" />
          <F ctx={ctx} id="documents.anytopdf_token" />
          <ConversionNodeCheck ctx={ctx} />
          <TikaSettings ctx={ctx} />
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
  const baseUrl = ctx.state("llm.base_url");
  const model = ctx.state("llm.model");
  // Test what is on screen, saved or not; the saved key goes only to the saved address
  const test = useMutation({
    mutationFn: () =>
      data(
        Admin.testLlm({
          client,
          body: {
            base_url: String(baseUrl.value ?? "") || null,
            model: String(model.value ?? "") || null,
            api_key: (key.value as string | undefined) || null,
          },
        }),
      ),
  });
  // The setup wizard's provider step: a server found nearby or a cloud provider fills in the address and the model,
  // and a cloud provider's key is asked for next
  const canPick = !baseUrl.locked && !model.locked;
  const { servers, cloud, looking } = useModelProviders(canPick);
  const at = String(baseUrl.value ?? "");
  const here = providerAt([...servers, ...cloud], at);
  const [askKey, setAskKey] = useState(0);
  const pick = (p: ModelProvider) => {
    if (p.key_url && !key.locked && !providerAt([p], at)) setAskKey((n) => n + 1); // a new provider: its key next
    baseUrl.onChange(p.base_url);
    model.onChange(p.model);
  };
  return (
    <>
      {canPick && <ModelProviderPicker servers={servers} cloud={cloud} looking={looking} baseUrl={at} onPick={pick} />}
      <F ctx={ctx} id="llm.base_url" />
      <F ctx={ctx} id="llm.model" suggestions={here?.models} />
      <SecretSetting
        key={ctx.view.llm?.updated_at ?? "none"}
        label="API key"
        isSet={Boolean(secret.set)}
        saved={secret}
        updatedBy={ctx.view.llm?.updated_by}
        updatedAt={ctx.view.llm?.updated_at}
        value={key.value as string | undefined}
        onChange={(x) => key.onChange(x)}
        hint={here?.key_url ? <KeyLink provider={here} /> : undefined}
        focus={askKey}
      />
      <div className="grid gap-3 sm:grid-cols-2">
        <F ctx={ctx} id="llm.max_chars" />
        <F ctx={ctx} id="llm.timeout" />
      </div>
      <More
        ctx={ctx}
        ids={["llm.api_key_env", "llm.chat_models"]}
        title="Models people may pick, and a key from the environment"
      >
        <F ctx={ctx} id="llm.chat_models" />
        <F ctx={ctx} id="llm.api_key_env" hint="Used only when no key is stored above" />
      </More>
      <More
        ctx={ctx}
        ids={[
          "llm.describe_engine",
          "llm.vision_model",
          "llm.florence_model",
          "llm.moondream_model",
          "llm.describe_max",
        ]}
        title="Describing pictures"
      >
        <F ctx={ctx} id="llm.describe_engine" />
        <div className="grid gap-3 sm:grid-cols-2">
          {ctx.state("llm.describe_engine").value === "florence-2" ? (
            <F ctx={ctx} id="llm.florence_model" />
          ) : ctx.state("llm.describe_engine").value === "moondream" ? (
            <F ctx={ctx} id="llm.moondream_model" />
          ) : (
            <F ctx={ctx} id="llm.vision_model" />
          )}
          <F ctx={ctx} id="llm.describe_max" />
        </div>
      </More>
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

/** The compose files' `tika` service, as Lens's containers reach it. */
const TIKA_SERVICE = "http://tika:9998";

function TikaSettings({ ctx }: { ctx: BodyCtx }) {
  const client = useApiClient();
  const url = ctx.state("documents.tika_url");
  const test = useMutation({ mutationFn: () => data(Admin.testTika({ client })) });
  const saved = Boolean(ctx.view.documents?.values?.tika_url);
  return (
    <>
      <F ctx={ctx} id="documents.tika_url" />
      {!url.value && (
        <div>
          <Button size="sm" onClick={() => url.onChange(TIKA_SERVICE)}>
            Use the Tika service next to Lens
          </Button>
        </div>
      )}
      {(saved || url.value) && (
        <>
          <F ctx={ctx} id="documents.tika_text" />
          <F ctx={ctx} id="documents.tika_metadata" />
        </>
      )}
      {saved && (
        <div className="flex flex-wrap items-center gap-2.5">
          <Button size="sm" icon={<PlugZap />} onClick={() => test.mutate()} disabled={test.isPending}>
            {test.isPending ? "Checking…" : "Test"}
          </Button>
          <span className="text-[12px] text-fg-muted">
            {ctx.dirty
              ? "Uses the saved settings, not your unsaved changes"
              : "Asks the Tika server which version it is"}
          </span>
        </div>
      )}
      {test.data &&
        (test.data.ok ? (
          <Banner tone="success" title="The Tika server answers.">
            {test.data.version} ({test.data.ms} ms)
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
  const promSecret = (saved.prometheus_token ?? {}) as { set?: boolean };
  const promToken = ctx.state("telemetry.prometheus_token");
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
        saved={secret}
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
        token counts, durations and estimated cost. Never asset, prompt or answer text, file names, titles, namespace
        names, people or addresses. Each worker follows these settings on its own.
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
      <Sub>Prometheus</Sub>
      <p className="-mt-2 text-[12.5px] leading-[1.45] text-fg-secondary">
        For Grafana and other Prometheus tools: jobs, imports, model calls, cost, budgets, workers and disk, served at{" "}
        <code className="font-mono">/metrics</code> on this address with the same privacy as telemetry. A scraper sends
        the token as <code className="font-mono">Authorization: Bearer</code>. Charts and history runs Prometheus and
        Grafana next to Lens, ready made, with charts of Lens and this machine, and sets up the token itself
        (docs/prometheus.md).
      </p>
      <MonitoringSwitch />
      {Boolean(saved.prometheus) && !promSecret.set && !promToken.value && (
        <Banner tone="error" title="On, but there is no scrape token.">
          Set one below; until then /metrics answers no one.
        </Banner>
      )}
      <F ctx={ctx} id="telemetry.prometheus" />
      <SecretSetting
        key={`prom-${t?.updated_at ?? "none"}`}
        label={
          locked.includes("prometheus_token") ? "Scrape token (set by LENS_PROMETHEUS_TOKEN in .env)" : "Scrape token"
        }
        placeholder="16 or more letters and digits, e.g. from openssl rand -hex 32"
        isSet={Boolean(promSecret.set)}
        saved={promSecret}
        updatedBy={t?.updated_by}
        updatedAt={t?.updated_at}
        value={promToken.value as string | undefined}
        onChange={(x) => promToken.onChange(x)}
      />
      <F ctx={ctx} id="telemetry.grafana_url" />
    </>
  );
}

function LogsBody({ ctx }: { ctx: BodyCtx }) {
  const client = useApiClient();
  const l = ctx.view.logs;
  const saved = l?.values ?? {};
  const status = useQuery({
    queryKey: ["log-status", l?.updated_at ?? null],
    queryFn: () => data(Admin.logStatus({ client })),
    refetchInterval: 15_000,
  });
  const test = useMutation({ mutationFn: () => data(Admin.testLogs({ client })) });
  const s = status.data;
  const host = (saved.syslog_host as string | null | undefined) ?? null;
  const on = Boolean(saved.syslog);
  const failing = Boolean(s?.last_error && (!s.last_sent || (s.error_at ?? 0) > s.last_sent));
  return (
    <>
      {on && s?.syslog ? (
        <Banner tone={failing ? "error" : "success"} title={`Sending to syslog at ${s.target}`}>
          {failing
            ? `Can’t reach it: ${s.last_error}. Lines wait and are tried again${s.dropped ? `; ${s.dropped} dropped so far` : ""}.`
            : `${s.sent} line${s.sent === 1 ? "" : "s"} sent from the server${s.dropped ? `, ${s.dropped} dropped` : ""}. Each worker sends its own.`}
        </Banner>
      ) : (
        <Banner title="Logs stay on this machine.">
          Lines go to the console and to a file per process. Turn on syslog below to send them to a log server too.
        </Banner>
      )}
      <div className="grid gap-3 sm:grid-cols-2">
        <F ctx={ctx} id="logs.level" />
        <F ctx={ctx} id="logs.format" />
      </div>
      <F ctx={ctx} id="logs.levels" />
      <F ctx={ctx} id="logs.file" />
      <div className="grid gap-3 sm:grid-cols-2">
        <F ctx={ctx} id="logs.file_mb" />
        <F ctx={ctx} id="logs.file_keep" />
      </div>
      <F ctx={ctx} id="logs.syslog" />
      <div className="grid gap-3 sm:grid-cols-[1fr_120px_120px]">
        <F ctx={ctx} id="logs.syslog_host" />
        <F ctx={ctx} id="logs.syslog_port" />
        <F ctx={ctx} id="logs.syslog_transport" />
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <F ctx={ctx} id="logs.syslog_level" />
        <F ctx={ctx} id="logs.syslog_facility" />
        <F ctx={ctx} id="logs.syslog_format" />
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <F ctx={ctx} id="logs.syslog_framing" />
        <F ctx={ctx} id="logs.syslog_app_name" />
        <F ctx={ctx} id="logs.syslog_verify" />
      </div>
      <p className="text-[12.5px] leading-[1.45] text-fg-secondary">
        RFC 5424 syslog, as rsyslog, syslog-ng, Graylog, Loki’s syslog receiver and most log tools take it. Keys, tokens
        and passwords are replaced with *** in every line before it is written or sent.
      </p>
      <div className="flex flex-wrap items-center gap-2.5">
        <Button size="sm" icon={<PlugZap />} onClick={() => test.mutate()} disabled={test.isPending || !host}>
          {test.isPending ? "Sending…" : "Send a test line"}
        </Button>
        <span className="text-[12px] text-fg-muted">
          {ctx.dirty
            ? "Uses the saved settings, not your unsaved changes"
            : "Sends one line, even while sending is off"}
        </span>
      </div>
      {test.data &&
        (test.data.ok ? (
          <Banner tone="success" title="The test line was sent.">
            {saved.syslog_transport === "udp"
              ? "UDP doesn’t confirm delivery: look for it on the server."
              : `The server took it in ${test.data.ms} ms.`}
          </Banner>
        ) : (
          <Banner tone="error" title="The test failed.">
            {test.data.error}
          </Banner>
        ))}
      {test.isError && <Banner tone="error">{test.error.message}</Banner>}
      <LogViewer />
    </>
  );
}

function AnalyticsBody({ ctx }: { ctx: BodyCtx }) {
  const client = useApiClient();
  const a = ctx.view.analytics;
  const saved = a?.values ?? {};
  const secret = (saved.api_secret ?? {}) as { set?: boolean };
  const apiSecret = ctx.state("analytics.api_secret");
  const locked = a?.locked ?? [];
  const status = useQuery({
    queryKey: ["analytics-status", a?.updated_at ?? null],
    queryFn: () => data(Admin.analyticsStatus({ client })),
    refetchInterval: 60_000,
  });
  const test = useMutation({ mutationFn: () => data(Admin.testAnalytics({ client })) });
  const s = status.data;
  const on = Boolean(saved.enabled);
  const id = (saved.measurement_id as string | null | undefined) ?? null;
  return (
    <>
      <p className="text-[12.5px] leading-[1.45] text-fg-secondary">
        Active people, churn, retention and feature use are on the{" "}
        <Link href="/analytics" className="font-semibold text-fg-accent hover:underline">
          Analytics
        </Link>{" "}
        page, worked out on this server whether or not Google Analytics is on. This section only chooses whether the
        daily events also go to your Google Analytics property.
      </p>
      {on && s?.configured ? (
        <Banner tone={s.last_error ? "error" : "success"} title={`On: sending to ${id}`}>
          {s.last_error
            ? s.last_error
            : s.last_sent_at
              ? `Last sent ${new Date(s.last_sent_at).toLocaleString()}: ${(s.last_sent ?? []).join(", ")}`
              : "Nothing sent yet; the first events go with the next daily check."}
        </Banner>
      ) : on ? (
        <Banner tone="error" title="On, but not set up.">
          Set the measurement id and the API secret below; until then nothing is sent.
        </Banner>
      ) : (
        <Banner title="Google Analytics is off: nothing is sent.">
          Turn it on here, in the setup wizard or with <code className="font-mono">LENS_ANALYTICS=on</code> in .env.
          Events go only to the GA4 property whose id and secret you set.
        </Banner>
      )}
      <F ctx={ctx} id="analytics.enabled" />
      <div className="grid gap-3 sm:grid-cols-2">
        <F ctx={ctx} id="analytics.measurement_id" />
        <F ctx={ctx} id="analytics.region" />
      </div>
      <SecretSetting
        key={a?.updated_at ?? "none"}
        label={
          locked.includes("api_secret")
            ? "Measurement Protocol API secret (set by LENS_GA_API_SECRET in .env)"
            : "Measurement Protocol API secret"
        }
        placeholder="From GA: Admin → Data streams → your stream → Measurement Protocol API secrets"
        isSet={Boolean(secret.set)}
        saved={secret}
        updatedBy={a?.updated_by}
        updatedAt={a?.updated_at}
        value={apiSecret.value as string | undefined}
        onChange={(x) => apiSecret.onChange(x)}
      />
      <F ctx={ctx} id="analytics.events" />
      <F ctx={ctx} id="analytics.inactive_days" />
      <p className="text-[12.5px] leading-[1.45] text-fg-secondary">
        What is sent, once a day: a random id for this install{s?.client_id ? ` (${s.client_id})` : ""}, the Lens
        version and platform, and counts. Never names, emails, account ids, titles, file names, namespaces, prompts or
        any text. In GA, the Retention report and a cohort exploration on lens_daily_active give churn across your
        installs.
      </p>
      <div className="flex flex-wrap items-center gap-2.5">
        <Button size="sm" icon={<PlugZap />} onClick={() => test.mutate()} disabled={test.isPending || !id}>
          {test.isPending ? "Sending…" : "Send a test event"}
        </Button>
        <span className="text-[12px] text-fg-muted">
          {ctx.dirty
            ? "Uses the saved settings, not your unsaved changes"
            : "GA’s validation server checks one lens_test event without recording it"}
        </span>
      </div>
      {test.data &&
        (test.data.ok ? (
          <Banner tone="success" title="Google Analytics accepted the test event." />
        ) : (
          <Banner tone="error" title="The test failed.">
            {test.data.error ?? (test.data.messages ?? []).join(" · ")}
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
          Encrypted files still play, seek, download and are processed as before. Their text, search and the rest of the
          database aren’t covered; put the data volume on an encrypted disk for those.
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

/** Settings › Secrets: the Vaultwarden vault secret fields can refer to (docs/secrets.md), a check of its sign-in, and
 * the fields that refer to it with whether each could be read. Never a value. */
function SecretsBody({ ctx }: { ctx: BodyCtx }) {
  const client = useApiClient();
  const s = ctx.view.secrets;
  const secret = (k: string) => (s?.values?.[k] ?? {}) as { set?: boolean };
  const clientSecret = ctx.state("secrets.vaultwarden_client_secret");
  const password = ctx.state("secrets.vaultwarden_password");
  const saved = Boolean(s?.values?.vaultwarden_url);
  const refs = useQuery({
    queryKey: ["secrets", s?.updated_at ?? null],
    queryFn: () => data(Admin.getSecrets({ client })),
  });
  const test = useMutation({
    mutationFn: () => data(Admin.testSecrets({ client, body: { provider: "vaultwarden" } })),
  });
  const where = (section: string, key: string) => {
    const f = FIELDS.find((x) => x.section === section && x.key === key);
    const sec = SECTIONS.find((x) => x.backend.includes(section));
    return { label: f?.label ?? `${section}.${key}`, section: sec, href: sec ? settingHref(sec.id, f) : null };
  };
  const references = refs.data?.references ?? [];
  return (
    <>
      <p className="text-[12.5px] leading-[1.45] text-fg-secondary">
        Any secret field in Settings can hold a reference to an item in your vault, like vaultwarden://OpenAI/password,
        instead of the secret. Lens reads the value and keeps only the reference; nobody sees the value, agents
        included. Give Lens an account of its own on the vault, and put the items it may read in one folder.
      </p>
      <F ctx={ctx} id="secrets.vaultwarden_url" />
      <div className="grid gap-3 sm:grid-cols-2">
        <F ctx={ctx} id="secrets.vaultwarden_email" />
        <F ctx={ctx} id="secrets.vaultwarden_client_id" />
      </div>
      <SecretSetting
        key={`client-${s?.updated_at ?? "none"}`}
        label="API key client_secret"
        placeholder="Paste the API key’s client_secret"
        isSet={Boolean(secret("vaultwarden_client_secret").set)}
        updatedBy={s?.updated_by}
        updatedAt={s?.updated_at}
        value={clientSecret.value as string | undefined}
        onChange={(x) => clientSecret.onChange(x)}
      />
      <SecretSetting
        key={`password-${s?.updated_at ?? "none"}`}
        label="Master password"
        placeholder="The account’s master password"
        isSet={Boolean(secret("vaultwarden_password").set)}
        updatedBy={s?.updated_by}
        updatedAt={s?.updated_at}
        value={password.value as string | undefined}
        onChange={(x) => password.onChange(x)}
        hint="The vault encrypts its items end to end, so it opens them"
      />
      <More
        ctx={ctx}
        ids={["secrets.vaultwarden_folder", "secrets.cache_minutes"]}
        title="The folder Lens reads, and how often"
      >
        <div className="grid gap-3 sm:grid-cols-2">
          <F ctx={ctx} id="secrets.vaultwarden_folder" />
          <F ctx={ctx} id="secrets.cache_minutes" />
        </div>
      </More>
      {saved && (
        <>
          <div className="flex flex-wrap items-center gap-2.5">
            <Button size="sm" icon={<PlugZap />} onClick={() => test.mutate()} disabled={test.isPending}>
              {test.isPending ? "Signing in…" : "Test"}
            </Button>
            <span className="text-[12px] text-fg-muted">
              {ctx.dirty
                ? "Uses the saved settings, not your unsaved changes"
                : "Signs in to the vault and counts the items Lens may read"}
            </span>
          </div>
          {test.data &&
            (test.data.ok ? (
              <Banner tone="success" title="Lens signs in to the vault.">
                {test.data.items ?? 0} item(s) in the folder {test.data.folder} of {test.data.account}.
              </Banner>
            ) : (
              <Banner tone="error" title="Not yet.">
                {test.data.error}
              </Banner>
            ))}
          {test.isError && <Banner tone="error">{test.error.message}</Banner>}
        </>
      )}
      <Sub>Fields that refer to the vault</Sub>
      {refs.isPending ? (
        <Skeleton className="h-10" />
      ) : references.length === 0 ? (
        <p className="text-[12.5px] leading-[1.45] text-fg-muted">
          None yet. In any secret field, pick From Vaultwarden, or paste a reference like vaultwarden://Item/field.
        </p>
      ) : (
        <ul className="flex flex-col divide-y divide-border rounded-sm border border-border">
          {references.map((r) => {
            const w = where(r.section, r.key);
            return (
              <li key={`${r.section}.${r.key}`} className="flex flex-col gap-0.5 px-3 py-2 text-[13px]">
                <span className="flex flex-wrap items-baseline gap-x-2">
                  {w.href ? (
                    <Link href={w.href} className="font-semibold text-fg-accent hover:underline">
                      {w.section?.label} › {w.label}
                    </Link>
                  ) : (
                    <span className="font-semibold">{w.label}</span>
                  )}
                  <span className="font-mono text-[12.5px] text-fg-secondary">{r.ref}</span>
                </span>
                <span className="text-[12px] text-fg-muted">
                  <ReferenceStatus saved={r} />
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </>
  );
}

function BackupsBody({ ctx }: { ctx: BodyCtx }) {
  const client = useApiClient();
  const conns = useQuery({ queryKey: ["sources"], queryFn: () => data(Sources.listSources({ client })) });
  const storage = (conns.data ?? []).filter((c) => !NOT_STORAGE.has(c.type));
  const sched = ctx.state("backups.schedule");
  const on = Boolean(String(sched.value ?? "").trim());
  return (
    <>
      <BackupsPanel />
      <F ctx={ctx} id="backups.schedule" />
      <div className="-mt-1 flex flex-wrap gap-1.5" role="group" aria-label="Common schedules">
        {[{ value: "", label: "Off" }, ...BACKUP_SCHEDULES].map((o) => (
          <Button
            key={o.label}
            size="xs"
            variant={String(sched.value ?? "") === o.value ? "primary" : "secondary"}
            onClick={() => sched.onChange(o.value)}
          >
            {o.label}
          </Button>
        ))}
      </div>
      {on && (
        <div className="grid gap-3 sm:grid-cols-2">
          <F ctx={ctx} id="backups.timezone" />
          <F ctx={ctx} id="backups.keep" />
        </div>
      )}
      <F
        ctx={ctx}
        id="backups.connection"
        options={[
          { value: "", label: "Nowhere else: only this machine" },
          ...storage.map((c) => ({ value: String(c.id), label: `${c.name} (${c.type})` })),
        ]}
      />
      <div>
        <AddConnection onAdded={(id) => ctx.state("backups.connection").onChange(String(id))} />
      </div>
      {Boolean(ctx.state("backups.connection").value) && <F ctx={ctx} id="backups.folder" />}
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
        (test.data.ok && test.data.limited ? (
          <Banner tone="warning" title="It works, but it’s rate limited.">
            The connection is {test.data.limited}.
          </Banner>
        ) : test.data.ok ? (
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
          saved={secret}
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
        asset’s metadata go within a minute; everything is compared on the schedule above, so what analysis changed, new
        assets and deletions follow. The workers send it.
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

function SmbBody({ ctx }: { ctx: BodyCtx }) {
  const client = useApiClient();
  const status = useQuery({
    queryKey: ["smb-status"],
    queryFn: () => data(Admin.smbStatus({ client })),
    refetchInterval: 10_000,
  });
  const s = status.data;
  const host = typeof window === "undefined" ? "<lens-machine>" : window.location.hostname;
  const fill = (x: string) => x.replaceAll("<lens-machine>", host);
  return (
    <>
      {s && s.enabled && (
        <Banner
          tone={s.phase === "running" ? "success" : s.phase === "error" ? "error" : "info"}
          title={s.phase === "running" ? "Sharing" : s.phase === "error" ? "Not sharing" : "Starting"}
        >
          {s.phase === "running" ? (
            <>
              Open <code className="font-mono">{fill(s.address)}</code> in Finder (Go › Connect to Server) or a Linux
              file manager, or mount it with <code className="font-mono">{fill(s.mount)}</code>. Sign in with a file
              access key: its id is the user name, its secret the password.
            </>
          ) : (
            (s.error ??
            (s.installed ? "A worker starts it within seconds." : "Fetching the SMB server, then a worker starts it."))
          )}
        </Banner>
      )}
      <F ctx={ctx} id="smb.enabled" />
      <F ctx={ctx} id="smb.allowed_networks" />
      <p className="text-[12.5px] leading-[1.45] text-fg-secondary">
        With Docker, the worker container publishes it on port {s?.port ?? 1445} of this machine, reachable from this
        machine only unless <code className="font-mono">LENS_BIND</code> or{" "}
        <code className="font-mono">LENS_SHARE_BIND</code> opens it to the network. Windows only connects to port 445:
        install with <code className="font-mono">LENS_SMB_PORT=445</code> in front of the install line for it.
      </p>
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
      <div className="grid gap-3 sm:grid-cols-2">
        <F ctx={ctx} id="sensors.mqtt_web" />
        <F ctx={ctx} id="sensors.mqtt_web_port" />
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

function NfsBody({ ctx }: { ctx: BodyCtx }) {
  const client = useApiClient();
  const status = useQuery({
    queryKey: ["nfs-status"],
    queryFn: () => data(Admin.nfsStatus({ client })),
    refetchInterval: 10_000,
  });
  const users = useQuery({ queryKey: ["users"], queryFn: () => data(Users.listUsers({ client })), staleTime: 15_000 });
  const s = status.data;
  return (
    <>
      {s && s.enabled && (
        <Banner
          tone={s.phase === "running" ? "success" : s.phase === "error" ? "error" : "info"}
          title={s.phase === "running" ? `Sharing as ${s.account}` : s.phase === "error" ? "Not sharing" : "Starting"}
        >
          {s.phase === "running" ? (
            <>
              Mount it on Linux with <code className="font-mono">{s.mount}</code>
              {s.refused
                ? `. Turned away ${s.refused} connection${s.refused === 1 ? "" : "s"} from other networks.`
                : "."}
            </>
          ) : (
            (s.error ?? "A worker starts it within seconds.")
          )}
        </Banner>
      )}
      <F ctx={ctx} id="nfs.enabled" />
      <F
        ctx={ctx}
        id="nfs.account"
        options={[
          { value: "", label: "The first admin" },
          ...(users.data ?? []).filter((u) => !u.disabled).map((u) => ({ value: String(u.id), label: u.email })),
        ]}
      />
      <F ctx={ctx} id="nfs.allowed_networks" />
      <F ctx={ctx} id="nfs.port" />
      <p className="text-[12.5px] leading-[1.45] text-fg-secondary">
        With Docker, the worker container publishes it on port {s?.port ?? 12049} of this machine, reachable from this
        machine only unless <code className="font-mono">LENS_BIND</code> or{" "}
        <code className="font-mono">LENS_SHARE_BIND</code> opens it to the network; set{" "}
        <code className="font-mono">LENS_NFS_PORT</code> in .env to use another port.
      </p>
    </>
  );
}

type ShareServer = {
  enabled: boolean;
  port: number;
  service?: { running?: boolean | null; port?: number | null; error?: string | null } | null;
};

function shareText(name: string, st: ShareServer): string {
  const svc = st.service;
  if (svc?.error) return `${name} can’t start: ${svc.error}`;
  if (svc?.running) return `${name} is serving on port ${svc.port}`;
  return st.enabled ? `${name} is starting…` : `${name} is off`;
}

function SharesBody({ ctx }: { ctx: BodyCtx }) {
  const client = useApiClient();
  const status = useQuery({
    queryKey: ["shares", "s3", ctx.view.shares?.updated_at ?? null],
    queryFn: () => data(Shares.s3Status({ client })),
    refetchInterval: 15_000,
  });
  const st = status.data;
  const failed = Boolean(st?.service?.error || st?.webdav.service?.error);
  const running = Boolean(st?.service?.running || st?.webdav.service?.running);
  const on = ctx.values["shares.enabled"] === true;
  return (
    <>
      {st && (
        <Banner
          tone={failed ? "error" : running ? "success" : "info"}
          title={running || failed ? "File shares" : "Not shared"}
          action={
            <Button asChild size="xs" variant="secondary">
              <Link href="/account/tokens#s3">Your access keys</Link>
            </Button>
          }
        >
          {on
            ? `${shareText("S3", st)}. ${shareText("WebDAV", st.webdav)}. People sign in with an access key from their API tokens page.`
            : "Turn it on below; the workers start listening within seconds."}
        </Banner>
      )}
      <F ctx={ctx} id="shares.enabled" />
      <div className="grid gap-3 sm:grid-cols-2">
        <F ctx={ctx} id="shares.s3" />
        <F ctx={ctx} id="shares.s3_port" />
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <F ctx={ctx} id="shares.webdav" />
        <F ctx={ctx} id="shares.webdav_port" />
      </div>
      <F ctx={ctx} id="shares.remote" />
      <p className="text-[12.5px] leading-[1.45] text-fg-secondary">
        With Docker, the worker container publishes 9555 (S3) and 9556 (WebDAV) where the web app is; set{" "}
        <code className="font-mono">LENS_S3_PORT</code> or <code className="font-mono">LENS_WEBDAV_PORT</code> in .env
        to publish them on other ports of the host.
        {on ? "" : " Nothing listens until it’s on."}
      </p>
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
          saved={m?.values?.password}
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

const OTHER_DNS = "__other";

/** Settings › HTTPS, when no server process has the HTTPS ports: open them through the upgrade agent (Lens restarts),
 * or the line to run on the machine. */
function HttpsPorts({
  status: s,
  open,
  busy,
  error,
}: {
  status: HttpsStatus;
  open: () => void;
  busy: boolean;
  error?: string;
}) {
  const run = s.reconfigure;
  const going = run && !run.error && ["queued", "running"].includes(run.state);
  if (going)
    return (
      <Banner title="Opening the HTTPS ports.">
        Lens is setting itself up again with ports 443 and 80 and restarts; this page follows when it’s back.
        {(run.log ?? []).length > 0 && (
          <pre className="mt-2 max-h-40 overflow-auto rounded-sm bg-surface-neutral p-2 font-mono text-[11.5px]">
            {(run.log ?? []).join("\n")}
          </pre>
        )}
      </Banner>
    );
  return (
    <Banner tone="warning" title="No server process has the HTTPS ports yet.">
      {run?.error && <span className="block text-red-dark">The last try didn’t work: {run.error}</span>}
      {error && <span className="block text-red-dark">{error}</span>}
      {s.agent ? (
        <>
          Lens opens ports 443 and 80 on this machine and restarts, keeping them through upgrades.
          <span className="mt-2 block">
            <Button size="sm" onClick={open} disabled={busy}>
              Open the HTTPS ports
            </Button>
          </span>
        </>
      ) : (
        <>
          Run this on the machine Lens runs on: it opens ports 443 and 80 and keeps them through upgrades.
          <pre className="mt-2 overflow-auto rounded-sm bg-surface-neutral p-2 font-mono text-[11.5px]">{s.manual}</pre>
        </>
      )}
    </Banner>
  );
}

/** Settings › HTTPS: the domains Lens serves over https://, how it gets their certificates, and how that's going. */
function HttpsBody({ ctx }: { ctx: BodyCtx }) {
  const client = useApiClient();
  const t = ctx.view.https;
  const on = ctx.state("https.enabled").value === true;
  const challenge = (ctx.state("https.challenge").value as string | undefined) ?? "http";
  const ca = (ctx.state("https.ca").value as string | undefined) ?? "both";
  const provider = ctx.state("https.dns_provider");
  const creds = ctx.state("https.dns_credentials");
  const eab = ctx.state("https.eab_hmac_key");
  const clientCa = ctx.state("https.client_ca");
  const clientAuth = (ctx.state("https.client_auth").value as string | undefined) ?? "off";
  const [fields, setFields] = useState<Record<string, string>>({});
  const [other, setOther] = useState(false);
  const status = useQuery({
    queryKey: ["https-status", t?.updated_at ?? null],
    queryFn: () => data(Admin.httpsStatus({ client })),
    refetchInterval: (q) =>
      !q.state.data?.enabled ? false : q.state.data.certificates?.length && !q.state.data.error ? 30_000 : 3_000,
  });
  const providers = useQuery({
    queryKey: ["https-providers"],
    queryFn: () => data(Admin.httpsProviders({ client })),
    enabled: challenge === "dns",
    staleTime: Infinity,
  });
  const qc = useQueryClient();
  const ports = useMutation({
    mutationFn: () => data(Admin.httpsPorts({ client, body: { open: true } })),
    onSuccess: (got) => qc.setQueryData(["https-status", t?.updated_at ?? null], got),
  });
  const s = status.data;
  const secret = (k: string) => Boolean(((t?.values?.[k] ?? {}) as { set?: boolean }).set);
  const prov = (provider.value as string | undefined) ?? "";
  const known = providers.data?.find((p) => p.name === prov);
  const choice = other || (prov && providers.data && !known) ? OTHER_DNS : prov;
  const setField = (name: string, value: string) => {
    const next = { ...fields, [name]: value };
    setFields(next);
    const filled = Object.fromEntries(Object.entries(next).filter(([, v]) => v.trim() !== ""));
    creds.onChange(Object.keys(filled).length ? JSON.stringify(filled) : undefined);
  };
  const expiry = (iso: string) => new Date(iso).toLocaleDateString(undefined, { dateStyle: "medium" });
  return (
    <>
      {!s || !s.enabled ? (
        <Banner title="Off.">
          Lens answers over http:// only, or through a proxy or tunnel in front of it. Turn this on to serve it at your
          own domain over https://, where passkeys work too.
        </Banner>
      ) : !s.ports_open ? (
        <HttpsPorts status={s} open={() => ports.mutate()} busy={ports.isPending} error={ports.error?.message} />
      ) : s.error ? (
        <Banner tone="error" title={s.running ? "No certificate yet: Lens keeps trying." : "HTTPS isn’t running."}>
          {s.error}
        </Banner>
      ) : s.certificates?.length && s.url ? (
        <Banner tone="success" title="Serving over HTTPS.">
          <a href={s.url} target="_blank" rel="noreferrer" className="font-mono font-semibold hover:underline">
            {s.url}
          </a>
        </Banner>
      ) : (
        <Banner title="Getting the certificate.">
          Lens starts Caddy and asks the certificate authority within a few seconds.
        </Banner>
      )}
      {s?.enabled && (s.certificates ?? []).length > 0 && (
        <ul className="flex flex-col gap-1 text-[12.5px] text-fg-secondary">
          {(s.certificates ?? []).map((c) => (
            <li key={c.names.join(",")}>
              <span className="font-mono text-fg">{c.names.join(", ")}</span> · {c.issuer} · until {expiry(c.expires)},
              renewed on its own
            </li>
          ))}
        </ul>
      )}
      <F ctx={ctx} id="https.enabled" />
      <F ctx={ctx} id="https.domains" />
      <F ctx={ctx} id="https.email" />
      <F ctx={ctx} id="https.challenge" />
      {challenge === "dns" && (
        <>
          <Field label="DNS provider">
            {({ id }) => (
              <Select
                id={id}
                value={choice}
                onChange={(e) => {
                  const v = e.target.value;
                  setOther(v === OTHER_DNS);
                  setFields({});
                  creds.onChange(undefined);
                  provider.onChange(v === OTHER_DNS ? "" : v);
                }}
                options={[
                  { value: "", label: "Choose…" },
                  ...(providers.data ?? []).map((p) => ({ value: p.name, label: p.label })),
                  { value: OTHER_DNS, label: "Another caddy-dns plugin" },
                ]}
              />
            )}
          </Field>
          {choice === OTHER_DNS ? (
            <>
              <F
                ctx={ctx}
                id="https.dns_provider"
                label="Plugin name"
                hint="The provider’s name in caddy-dns, like hetzner."
              />
              <F ctx={ctx} id="https.dns_package" />
              <SecretSetting
                key={`creds-${t?.updated_at ?? "none"}`}
                label="Its fields, as JSON"
                placeholder='{"api_token": "…"}'
                isSet={secret("dns_credentials")}
                saved={t?.values?.dns_credentials}
                updatedBy={t?.updated_by}
                updatedAt={t?.updated_at}
                value={creds.value as string | undefined}
                onChange={(x) => creds.onChange(x)}
              />
            </>
          ) : (
            known && (
              <div className="grid gap-3 sm:grid-cols-2">
                {known.fields.map((f) => (
                  <Field key={f.name} label={f.label}>
                    {({ id }) => (
                      <Input
                        id={id}
                        type={f.secret ? "password" : "text"}
                        autoComplete="off"
                        mono
                        placeholder={secret("dns_credentials") ? "saved: type to replace" : ""}
                        value={fields[f.name] ?? ""}
                        onChange={(e) => setField(f.name, e.target.value)}
                      />
                    )}
                  </Field>
                ))}
              </div>
            )
          )}
          <p className="text-[12.5px] leading-[1.45] text-fg-muted">
            Lens fetches a Caddy built with this provider’s plugin the first time. The credentials are kept encrypted
            and given to Caddy only in a file only Lens can read.
          </p>
        </>
      )}
      <Sub>Who gets in</Sub>
      <F ctx={ctx} id="https.strict_sni" />
      <F ctx={ctx} id="https.client_auth" />
      {clientAuth !== "off" && (
        <Field
          label="CA that signs client certificates"
          error={ctx.state("https.client_ca").error}
          hint="PEM, one certificate or more. Only browsers and devices with a certificate it signed get in."
        >
          {({ id, describedBy, invalid }) => (
            <Textarea
              id={id}
              aria-describedby={describedBy}
              invalid={invalid}
              rows={5}
              className="font-mono text-[12px]"
              placeholder="-----BEGIN CERTIFICATE-----"
              value={(clientCa.value as string | undefined) ?? ""}
              onChange={(e) => clientCa.onChange(e.target.value)}
            />
          )}
        </Field>
      )}
      {clientAuth === "required" && (
        <p className="text-[12.5px] leading-[1.45] text-fg-muted">
          The web app’s own port (3000) doesn’t ask for a certificate: keep it to this machine (install with
          LENS_PUBLIC=0) so the only way in from elsewhere is HTTPS.
        </p>
      )}
      <F ctx={ctx} id="https.min_tls" />
      <details className="text-[13px]">
        <summary className="cursor-pointer font-semibold text-fg-secondary">More options</summary>
        <div className="mt-3 flex flex-col gap-4">
          <F ctx={ctx} id="https.ca" />
          {ca === "custom" && <F ctx={ctx} id="https.ca_url" />}
          {(ca === "both" || ca === "letsencrypt") && <F ctx={ctx} id="https.staging" />}
          {ca !== "letsencrypt" && (
            <div className="grid gap-3 sm:grid-cols-2">
              <F ctx={ctx} id="https.eab_key_id" />
              <SecretSetting
                key={`eab-${t?.updated_at ?? "none"}`}
                label="EAB HMAC key"
                placeholder="Paste the HMAC key"
                isSet={secret("eab_hmac_key")}
                saved={t?.values?.eab_hmac_key}
                updatedBy={t?.updated_by}
                updatedAt={t?.updated_at}
                value={eab.value as string | undefined}
                onChange={(x) => eab.onChange(x)}
              />
            </div>
          )}
          {challenge === "dns" && (
            <div className="grid gap-3 sm:grid-cols-2">
              <F ctx={ctx} id="https.dns_resolvers" />
              <F ctx={ctx} id="https.propagation" />
            </div>
          )}
          <F ctx={ctx} id="https.redirect_http" />
          <F ctx={ctx} id="https.hsts" />
          <F
            ctx={ctx}
            id="https.origin"
            hint={`Where Caddy reaches the web app from the server${s ? ` (now ${s.origin})` : ""}. Leave empty unless you moved it.`}
          />
          <div className="grid gap-3 sm:grid-cols-2">
            <F ctx={ctx} id="https.https_port" />
            <F ctx={ctx} id="https.http_port" />
          </div>
        </div>
      </details>
      {on && s && (s.log ?? []).length > 0 && (
        <details className="text-[12.5px]">
          <summary className="cursor-pointer font-semibold text-fg-secondary">
            What Caddy said{s.process ? ` (in ${s.process})` : ""}
          </summary>
          <pre className="mt-2 max-h-64 overflow-auto rounded-sm bg-surface-neutral p-2.5 font-mono text-[11.5px] leading-snug">
            {(s.log ?? []).join("\n")}
          </pre>
        </details>
      )}
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
            saved={t?.values?.api_token}
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
            saved={t?.values?.token}
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

/** Settings › Network: the Nebula mesh between Lens's machines (docs/mesh.md), how it runs on this machine right now,
 * and its settings. */
function NetworkBody({ ctx }: { ctx: BodyCtx }) {
  const client = useApiClient();
  const m = ctx.view.mesh;
  const status = useQuery({
    queryKey: ["mesh", m?.updated_at ?? null],
    queryFn: () => data(Mesh.meshStatus({ client })),
    refetchInterval: (q) => (!q.state.data?.enabled ? false : q.state.data.service.nebula ? 15_000 : 3_000),
  });
  const services = useServices();
  const s = status.data;
  const svc = s?.service;
  const agent = services.data?.services?.find((x) => x.profile === "mesh")?.agent;
  // the container says how it runs every few seconds; a minute without a word and it isn't running
  const stale = !svc?.seen_at || Date.now() - Date.parse(svc.seen_at) > 60_000;
  const down = Boolean(s?.enabled && svc && !svc.nebula && svc.available && !svc.error && stale);
  const log = svc?.log ?? [];
  return (
    <>
      {status.isError ? (
        <Banner tone="error">{status.error.message}</Banner>
      ) : !s ? (
        <Skeleton className="h-11 w-full" />
      ) : !s.enabled ? (
        <Banner title="Off.">
          Off until you add a node: a single machine needs no mesh. Adding one in{" "}
          <Link href="/settings/nodes" className="font-semibold text-fg-accent hover:underline">
            Settings › Nodes
          </Link>{" "}
          turns it on.
        </Banner>
      ) : svc?.nebula ? (
        <Banner tone="success" title="On, and running.">
          This machine is <span className="font-mono">{s.primary_ip}</span> on the mesh
          {svc.since ? `, Nebula started ${relative(svc.since)}` : ""}, with {s.nodes} node{s.nodes === 1 ? "" : "s"}.
        </Banner>
      ) : !svc?.available ? (
        <Banner tone="warning" title="On, but Nebula can’t run on this machine.">
          The mesh runs in a container of its own, which Docker installs have and this install doesn’t, so other
          machines can’t reach it.
        </Banner>
      ) : svc.error ? (
        <Banner tone="error" title="Nebula isn’t running.">
          {svc.error}
        </Banner>
      ) : down ? (
        <Banner tone="warning" title="On, but the mesh container isn’t running.">
          {agent
            ? "The machine’s upgrade agent starts it; that takes a minute or so."
            : "This machine has no upgrade agent to start it: run the line below on the machine."}
        </Banner>
      ) : (
        <Banner title="Starting.">
          The mesh container starts Nebula within a few seconds
          {svc.state === "waiting" ? ", once Lens has signed its key" : ""}.
        </Banner>
      )}
      {down && !agent && svc && <CodeBlock text={svc.manual} label="the line that starts the mesh container" />}
      {s && (
        <dl className="grid gap-x-4 gap-y-1.5 text-[13px] leading-[1.45] sm:grid-cols-[190px_minmax(0,1fr)]">
          <dt className="font-semibold text-fg-secondary">This machine on the mesh</dt>
          <dd className="font-mono">{s.primary_ip}</dd>
          <dt className="font-semibold text-fg-secondary">Certificate authority</dt>
          <dd className="min-w-0">
            {s.ca ? (
              <>
                <span className="break-all font-mono text-[12px]">{s.ca.fingerprint}</span>
                {s.ca.not_after ? ` · until ${shortDate(s.ca.not_after)}` : ""}
              </>
            ) : (
              "Made when you add the first node."
            )}
          </dd>
          <dt className="font-semibold text-fg-secondary">Machines</dt>
          <dd>
            {s.nodes} node{s.nodes === 1 ? "" : "s"}
            {s.removed ? `, ${s.removed} removed` : ""}
            {s.tokens ? `, ${s.tokens} join token${s.tokens === 1 ? "" : "s"} not used yet` : ""} ·{" "}
            <Link href="/settings/nodes" className="font-semibold text-fg-accent hover:underline">
              Nodes
            </Link>
          </dd>
          <dt className="font-semibold text-fg-secondary">Nebula</dt>
          <dd>{s.nebula}</dd>
        </dl>
      )}
      <F ctx={ctx} id="mesh.enabled" />
      <More
        ctx={ctx}
        ids={[
          "mesh.network",
          "mesh.port",
          "mesh.hosts",
          "mesh.cipher",
          "mesh.cert_days",
          "mesh.relay",
          "mesh.mtu",
          "mesh.log_level",
        ]}
        title="Addresses, port, cipher and certificates"
      >
        <F ctx={ctx} id="mesh.network" />
        <div className="grid gap-3 sm:grid-cols-2">
          <F ctx={ctx} id="mesh.port" />
          <F ctx={ctx} id="mesh.cert_days" />
        </div>
        <F ctx={ctx} id="mesh.hosts" />
        <div className="grid gap-3 sm:grid-cols-2">
          <F ctx={ctx} id="mesh.cipher" />
          <F ctx={ctx} id="mesh.mtu" />
        </div>
        <F ctx={ctx} id="mesh.relay" />
        <F ctx={ctx} id="mesh.log_level" />
      </More>
      {log.length > 0 && (
        <details className="text-[12.5px]">
          <summary className="cursor-pointer font-semibold text-fg-secondary">What Nebula said</summary>
          <pre className="mt-2 max-h-64 overflow-auto rounded-sm bg-surface-neutral p-2.5 font-mono text-[11.5px] leading-snug">
            {log.join("\n")}
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
            saved={b?.values?.token}
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
      <F ctx={ctx} id="bridge.safe_links" />
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

function TelegramBody({ ctx }: { ctx: BodyCtx }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const t = ctx.view.telegram;
  const token = ctx.state("telegram.token");
  const status = useQuery({
    queryKey: ["telegram-status", t?.updated_at ?? null],
    queryFn: () => data(Admin.telegramStatus({ client })),
    refetchInterval: 5_000, // people who write to the bot show up to let in
  });
  const test = useMutation({ mutationFn: () => data(Admin.testTelegram({ client })) });
  const saved = ((t?.values?.users ?? []) as string[]).filter(Boolean);
  const letIn = useMutation({
    mutationFn: (id: string) =>
      data(Admin.updateSettings({ client, path: { section: "telegram" }, body: { users: [...saved, id] } })),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["settings"] });
      await qc.invalidateQueries({ queryKey: ["telegram-status"] });
    },
  });
  const s = status.data;
  const waiting = (s?.people ?? []).filter((p) => !p.allowed);
  return (
    <>
      {s?.state === "running" ? (
        <Banner tone="success" title={s.bot ? `Listening as @${s.bot}.` : "Listening."}>
          {s.answered ? `Answered ${s.answered} message${s.answered === 1 ? "" : "s"} so far. ` : ""}
          {saved.length
            ? "Each person’s conversation is in Chat for the account it answers as."
            : "Nobody is let in yet: message the bot from Telegram, then let yourself in below."}
        </Banner>
      ) : s?.state === "error" ? (
        <Banner tone="error" title="The last look at Telegram failed.">
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
          Create a bot with @BotFather in Telegram (/newbot), paste its token below and turn this on.
        </Banner>
      )}
      {waiting.length > 0 && (
        <div className="grid gap-1.5">
          <span className="text-[12.5px] font-semibold text-fg-secondary">Wrote to the bot, not let in</span>
          <ul className="grid gap-1">
            {waiting.map((p) => (
              <li key={p.id} className="flex items-center gap-2.5 text-[13px]">
                <span>{p.name}</span>
                <span className="font-mono text-[12px] text-fg-muted">{p.id}</span>
                <Button size="sm" onClick={() => letIn.mutate(p.id)} disabled={letIn.isPending}>
                  Let in
                </Button>
              </li>
            ))}
          </ul>
          {letIn.isError && <Banner tone="error">{letIn.error.message}</Banner>}
        </div>
      )}
      <F ctx={ctx} id="telegram.enabled" />
      <SecretSetting
        key={t?.updated_at ?? "none"}
        label="Bot token"
        isSet={Boolean(((t?.values?.token ?? {}) as { set?: boolean }).set)}
        saved={t?.values?.token}
        updatedBy={t?.updated_by}
        updatedAt={t?.updated_at}
        value={token.value as string | undefined}
        onChange={(x) => token.onChange(x)}
      />
      <F ctx={ctx} id="telegram.users" />
      <F ctx={ctx} id="telegram.account" />
      <div className="grid gap-3 sm:grid-cols-2">
        <F ctx={ctx} id="telegram.name" />
        <F ctx={ctx} id="telegram.answer" />
      </div>
      <F ctx={ctx} id="telegram.approve" />
      {(s?.chats ?? []).length > 0 && (
        <div className="grid gap-1">
          <span className="text-[12.5px] font-semibold text-fg-secondary">Chats it has seen</span>
          <ul className="grid gap-0.5 text-[13px]">
            {(s?.chats ?? []).map((c) => (
              <li key={c.id}>
                {c.title} <span className="font-mono text-[12px] text-fg-muted">{c.id}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      <F ctx={ctx} id="telegram.rooms" />
      <F ctx={ctx} id="telegram.api_url" />
      <div className="flex flex-wrap items-center gap-2.5">
        <Button size="sm" icon={<PlugZap />} onClick={() => test.mutate()} disabled={test.isPending}>
          {test.isPending ? "Checking…" : "Check the bot"}
        </Button>
        {ctx.dirty && (
          <span className="text-[12px] text-fg-muted">Uses the saved settings, not your unsaved changes</span>
        )}
      </div>
      {test.data &&
        (test.data.ok ? (
          <Banner tone="success" title="Telegram knows the bot.">
            The token and the account it answers as check out.
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

function SlackToken({ ctx, id, label }: { ctx: BodyCtx; id: "bot_token" | "app_token"; label: string }) {
  const sl = ctx.view.slack;
  const st = ctx.state(`slack.${id}`);
  return (
    <SecretSetting
      key={sl?.updated_at ?? "none"}
      label={label}
      isSet={Boolean(((sl?.values?.[id] ?? {}) as { set?: boolean }).set)}
      saved={sl?.values?.[id]}
      updatedBy={sl?.updated_by}
      updatedAt={sl?.updated_at}
      value={st.value as string | undefined}
      onChange={(x) => st.onChange(x)}
    />
  );
}

function SlackBody({ ctx }: { ctx: BodyCtx }) {
  const client = useApiClient();
  const sl = ctx.view.slack;
  const status = useQuery({
    queryKey: ["slack-status", sl?.updated_at ?? null],
    queryFn: () => data(Admin.slackStatus({ client })),
    refetchInterval: 10_000,
  });
  const test = useMutation({ mutationFn: () => data(Admin.testSlack({ client })) });
  const s = status.data;
  return (
    <>
      {s?.state === "running" ? (
        <Banner tone="success" title={s.team ? `Connected to ${s.team}.` : "Connected."}>
          {s.answered ? `Answered ${s.answered} message${s.answered === 1 ? "" : "s"} so far. ` : ""}
          Message {s.bot ? `@${s.bot}` : "the app"}, invite it to a channel with /invite, or type {s.command}. Each
          person’s conversation is in Chat for the account it answers as.
        </Banner>
      ) : s?.state === "error" ? (
        <Banner tone="error" title="Slack isn’t answering.">
          {s.error}
        </Banner>
      ) : s?.state === "incomplete" ? (
        <Banner tone="error" title="On, but not ready.">
          It needs {s.error}.
        </Banner>
      ) : s?.state === "starting" ? (
        <Banner title="Starting.">A server process connects to Slack within a few seconds.</Banner>
      ) : (
        <Banner title="Off.">
          <a className="underline" href={s?.create_url} target="_blank" rel="noreferrer">
            Create the Slack app
          </a>{" "}
          (one click: it’s made from Lens’s manifest), install it to your workspace, then paste its two tokens below and
          turn this on.
        </Banner>
      )}
      <F ctx={ctx} id="slack.enabled" />
      <SlackToken ctx={ctx} id="bot_token" label="Bot token (xoxb-…, from OAuth & Permissions)" />
      <SlackToken
        ctx={ctx}
        id="app_token"
        label="App-level token (xapp-…, from Basic Information, with connections:write)"
      />
      {s?.create_url && s.state !== "off" && (
        <p className="text-[12.5px] text-fg-muted">
          <a className="underline" href={s.create_url} target="_blank" rel="noreferrer">
            Create the Slack app again
          </a>{" "}
          from Lens’s manifest, for another workspace or to get new permissions.
        </p>
      )}
      <F ctx={ctx} id="slack.account" />
      <div className="grid gap-3 sm:grid-cols-2">
        <F ctx={ctx} id="slack.name" />
        <F ctx={ctx} id="slack.answer" />
      </div>
      <F ctx={ctx} id="slack.users" />
      <F ctx={ctx} id="slack.approve" />
      <F ctx={ctx} id="slack.rooms" />
      <div className="flex flex-wrap items-center gap-2.5">
        <Button size="sm" icon={<PlugZap />} onClick={() => test.mutate()} disabled={test.isPending}>
          {test.isPending ? "Checking…" : "Check the app"}
        </Button>
        {ctx.dirty && (
          <span className="text-[12px] text-fg-muted">Uses the saved settings, not your unsaved changes</span>
        )}
      </div>
      {test.data &&
        (test.data.ok ? (
          <Banner tone="success" title="Slack knows the app.">
            Both tokens and the account it answers as check out.
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

/** Settings › Assistant email: the assistant's own mailbox, who has written to it, and the servers it uses. */
function MailboxBody({ ctx }: { ctx: BodyCtx }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const m = ctx.view.mailbox;
  const password = ctx.state("mailbox.password");
  const status = useQuery({
    queryKey: ["mailbox-status", m?.updated_at ?? null],
    queryFn: () => data(Admin.mailboxStatus({ client })),
    refetchInterval: 10_000, // people who write to it show up to let in
  });
  const test = useMutation({ mutationFn: () => data(Admin.testMailbox({ client })) });
  const saved = ((m?.values?.users ?? []) as string[]).filter(Boolean);
  const letIn = useMutation({
    mutationFn: (id: string) =>
      data(Admin.updateSettings({ client, path: { section: "mailbox" }, body: { users: [...saved, id] } })),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["settings"] });
      await qc.invalidateQueries({ queryKey: ["mailbox-status"] });
    },
  });
  const s = status.data;
  const waiting = (s?.people ?? []).filter((p) => !p.allowed);
  return (
    <>
      {s?.state === "running" ? (
        <Banner tone="success" title={s.address ? `Reading mail to ${s.address}.` : "Reading mail."}>
          {s.answered ? `Answered ${s.answered} email${s.answered === 1 ? "" : "s"} so far. ` : ""}
          {saved.length
            ? "Each email thread is a conversation in Chat for the account it answers as."
            : "Nobody is let in yet: email it, then let yourself in below."}
        </Banner>
      ) : s?.state === "error" ? (
        <Banner tone="error" title="The last look at the mailbox failed.">
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
          Give the assistant an address of its own at any provider, enter it and its password (an app password, for
          most), and turn this on. Mail that arrives after that is answered.
        </Banner>
      )}
      {waiting.length > 0 && (
        <div className="grid gap-1.5">
          <span className="text-[12.5px] font-semibold text-fg-secondary">Wrote to it, not let in</span>
          <ul className="grid gap-1">
            {waiting.map((p) => (
              <li key={p.id} className="flex flex-wrap items-center gap-2.5 text-[13px]">
                <span>{p.name}</span>
                <span className="font-mono text-[12px] text-fg-muted">{p.id}</span>
                {p.vouched ? (
                  <>
                    {p.waiting ? (
                      <span className="text-[12px] text-fg-muted">
                        {p.waiting} email{p.waiting === 1 ? "" : "s"} waiting
                      </span>
                    ) : null}
                    <Button size="sm" onClick={() => letIn.mutate(p.id)} disabled={letIn.isPending}>
                      Let in
                    </Button>
                  </>
                ) : (
                  <span className="text-[12px] text-fg-muted">
                    Their provider didn’t vouch for it, so it may not be from them.
                  </span>
                )}
              </li>
            ))}
          </ul>
          {letIn.isError && <Banner tone="error">{letIn.error.message}</Banner>}
        </div>
      )}
      <F ctx={ctx} id="mailbox.enabled" />
      <F ctx={ctx} id="mailbox.address" />
      <SecretSetting
        key={m?.updated_at ?? "none"}
        label="Password"
        placeholder="The mailbox’s password (an app password, for most providers)"
        isSet={Boolean(((m?.values?.password ?? {}) as { set?: boolean }).set)}
        saved={m?.values?.password}
        updatedBy={m?.updated_by}
        updatedAt={m?.updated_at}
        value={password.value as string | undefined}
        onChange={(x) => password.onChange(x)}
      />
      <F ctx={ctx} id="mailbox.users" />
      <F ctx={ctx} id="mailbox.account" />
      <div className="grid gap-3 sm:grid-cols-2">
        <F ctx={ctx} id="mailbox.imports" />
        <F ctx={ctx} id="mailbox.approve" />
      </div>
      <F ctx={ctx} id="mailbox.routes" />
      <details className="text-[13px]">
        <summary className="cursor-pointer font-semibold text-fg-secondary">
          Mail servers{s?.imap ? ` (now ${s.imap} and ${s.smtp})` : ""}
        </summary>
        <div className="mt-3 grid gap-4">
          <F ctx={ctx} id="mailbox.username" />
          <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_120px]">
            <F ctx={ctx} id="mailbox.imap_host" />
            <F ctx={ctx} id="mailbox.imap_port" />
          </div>
          <F ctx={ctx} id="mailbox.imap_security" />
          <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_120px]">
            <F ctx={ctx} id="mailbox.smtp_host" />
            <F ctx={ctx} id="mailbox.smtp_port" />
          </div>
          <F ctx={ctx} id="mailbox.smtp_security" />
          <div className="grid gap-3 sm:grid-cols-2">
            <F ctx={ctx} id="mailbox.folder" />
            <F ctx={ctx} id="mailbox.poll_seconds" />
          </div>
          <F ctx={ctx} id="mailbox.check" />
          <F ctx={ctx} id="mailbox.auth_server" />
          <F ctx={ctx} id="mailbox.name" />
        </div>
      </details>
      <div className="flex flex-wrap items-center gap-2.5">
        <Button size="sm" icon={<PlugZap />} onClick={() => test.mutate()} disabled={test.isPending}>
          {test.isPending ? "Checking…" : "Check the mailbox"}
        </Button>
        <span className="text-[12px] text-fg-muted">
          {ctx.dirty
            ? "Uses the saved settings, not your unsaved changes"
            : "Signs in to read and to send; nothing is read or sent"}
        </span>
      </div>
      {test.data &&
        (test.data.ok ? (
          <Banner tone="success" title="Lens can read and send mail there.">
            The mailbox and the account it answers as check out.
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
      saved={m?.values?.[id]}
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
            className="mt-2 w-fit bg-white p-2 font-mono text-[9px] leading-none text-black"
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

const LIMITS = [
  "ai.confirm_over_recordings",
  "ai.confirm_over_cost",
  "ai.price_in",
  "ai.price_out",
  "ai.max_steps",
  "ai.max_tool_calls",
  "ai.browser_steps",
  "ai.max_transcript_reads",
  "ai.compact_model",
  "ai.context_tokens",
  "ai.compact_at",
];

function AiBody({ ctx }: { ctx: BodyCtx }) {
  const tools = ctx.state("ai.disabled_tools");
  const off = ((tools.value as string[]) ?? []).filter((x) => AI_TOOLS.some((t) => t.name === x));
  const guards =
    Number(Boolean(ctx.form["ai.untrusted_marking"])) +
    Number(Boolean(ctx.form["ai.untrusted_asks"])) +
    Number((ctx.form["ai.injection_check"] ?? "patterns") !== "off");
  const enabled = Boolean(ctx.form["ai.tools"]);
  const tts = String(ctx.form["voice.tts_provider"] ?? "openai");
  return (
    <>
      <p className="text-[13px] leading-normal text-fg-secondary">
        The assistant uses the model set in LLM provider; it needs one that supports tool calls.
      </p>
      <F ctx={ctx} id="ai.tools" />
      <F ctx={ctx} id="ai.browser" />
      <F ctx={ctx} id="ai.refine_notes" />
      <F ctx={ctx} id="ai.organise_notes" />
      <F ctx={ctx} id="ai.compact" />
      <F ctx={ctx} id="ai.auto_title" />
      <F ctx={ctx} id="ai.answer_comments" />
      <More
        ctx={ctx}
        ids={["ai.untrusted_marking", "ai.untrusted_asks", "ai.injection_check"]}
        title={`Text from outside Lens (${guards} of 3 defences on)`}
      >
        <F ctx={ctx} id="ai.untrusted_marking" />
        <F ctx={ctx} id="ai.untrusted_asks" />
        <F ctx={ctx} id="ai.injection_check" />
      </More>
      <More
        ctx={ctx}
        ids={["ai.disabled_tools"]}
        title={`Tools it may use (${AI_TOOLS.length - off.length} of ${AI_TOOLS.length})`}
      >
        <ul className="flex flex-col gap-2" aria-label="Tools">
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
      </More>
      <WebSearch ctx={ctx} />
      <McpServers />
      <More
        ctx={ctx}
        ids={["research.max_tool_calls", "research.max_minutes", "research.model", "research.agents_skip_review"]}
        title="Deep research"
      >
        <p className="text-[13px] leading-normal text-fg-secondary">
          A goal (the Deep research switch in chat, or /research) gets a plan to approve, then runs in the background
          and brings back a cited report.
        </p>
        <div className="grid gap-3 sm:grid-cols-2">
          <F ctx={ctx} id="research.max_tool_calls" />
          <F ctx={ctx} id="research.max_minutes" />
          <F ctx={ctx} id="research.model" />
        </div>
        <F ctx={ctx} id="research.agents_skip_review" />
      </More>
      <More ctx={ctx} ids={LIMITS} title="Approvals, steps, prices and long conversations">
        <div className="grid gap-3 sm:grid-cols-2">
          <F ctx={ctx} id="ai.confirm_over_recordings" />
          <F ctx={ctx} id="ai.confirm_over_cost" />
          <F ctx={ctx} id="ai.price_in" />
          <F ctx={ctx} id="ai.price_out" />
          <F ctx={ctx} id="ai.max_steps" />
          <F ctx={ctx} id="ai.max_tool_calls" />
          <F ctx={ctx} id="ai.browser_steps" />
          <F ctx={ctx} id="ai.max_transcript_reads" />
          <F ctx={ctx} id="ai.compact_model" />
          <F ctx={ctx} id="ai.context_tokens" />
          <F ctx={ctx} id="ai.compact_at" />
        </div>
      </More>
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
          {(tts === "openai" || tts === "elevenlabs") && (
            <F
              ctx={ctx}
              id="voice.tts_model"
              hint={tts === "elevenlabs" ? "Empty: eleven_multilingual_v2" : undefined}
            />
          )}
          <VoicePicker
            provider={tts}
            savedProvider={String(ctx.values["voice.tts_provider"] ?? "openai")}
            state={ctx.state("voice.tts_voice")}
          />
          {tts === "openai" && <F ctx={ctx} id="voice.tts_base_url" />}
          {tts === "styletts2" && <F ctx={ctx} id="voice.styletts2_url" />}
        </div>
        {tts === "styletts2" ? (
          <p className="text-[12.5px] text-fg-secondary">
            Reads on this machine, in a voice like a short clip of someone speaking: put a few seconds of a .wav in the
            service’s voices folder and pick it here. Heavy, so it runs only when started with{" "}
            <code className="font-mono text-[12px]">docker compose --profile styletts2 up -d</code>. Tell listeners the
            speech is synthesized unless you may use the voice.
          </p>
        ) : tts === "say" ? (
          <p className="text-[12.5px] text-fg-secondary">
            Uses the voices installed on the Mac Lens runs on (System Settings → Accessibility → Spoken Content adds
            more). Not available when Lens runs in Docker.
          </p>
        ) : tts === "openai" ? (
          <SecretSetting
            key={ctx.view.voice?.updated_at ?? "none"}
            label="Speech server API key"
            isSet={Boolean(((ctx.view.voice?.values?.tts_api_key ?? {}) as { set?: boolean }).set)}
            saved={ctx.view.voice?.values?.tts_api_key}
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

/** Settings › AI assistant › Web search: off until an admin turns it on (DuckDuckGo in one click), then the provider,
 * its key or address, and how much it reads. */
function WebSearch({ ctx }: { ctx: BodyCtx }) {
  const search = ctx.state("web.search");
  const provider = String(ctx.form["web.search"] ?? "off");
  const on = provider !== "off";
  const keyed = provider === "brave" || provider === "tavily";
  const web = ctx.view.web;
  const toggle: FieldState = {
    ...search,
    value: on,
    onChange: (v) => search.onChange(v ? "duckduckgo" : "off"),
  };
  return (
    <div className="flex flex-col gap-3">
      <span className="text-[13px] font-bold leading-tight text-fg-strong">Web search</span>
      <SettingField
        spec={{
          section: "web",
          key: "search",
          label: "Let the assistant search the web and read pages",
          kind: "switch",
          hint: "For what Lens doesn’t hold; pages are cited like excerpts. Off: nothing leaves this server for it.",
        }}
        state={toggle}
      />
      {on && (
        <>
          <div className="grid gap-3 sm:grid-cols-2">
            <F ctx={ctx} id="web.search" options={ctx.spec("web.search").options?.filter((o) => o.value !== "off")} />
            <F ctx={ctx} id="web.results" />
            {provider === "searxng" && <F ctx={ctx} id="web.searxng_url" />}
          </div>
          {keyed && (
            <SecretSetting
              key={web?.updated_at ?? "none"}
              label={`${provider === "brave" ? "Brave Search" : "Tavily"} API key`}
              isSet={Boolean(((web?.values?.api_key ?? {}) as { set?: boolean }).set)}
              saved={web?.values?.api_key}
              updatedBy={web?.updated_by}
              updatedAt={web?.updated_at}
              value={ctx.state("web.api_key").value as string | undefined}
              onChange={(x) => ctx.state("web.api_key").onChange(x)}
              hint="Searches past the provider’s free allowance are charged by them; the activity log counts each one."
            />
          )}
          <p className="text-[12.5px] leading-normal text-fg-secondary">
            {provider === "duckduckgo"
              ? "DuckDuckGo has no API: Lens reads its results page with ddgs, an unofficial client (about 30 MB, fetched now; Settings › Components shows it) that can stop working when the page changes. SearXNG or a keyed provider is steadier. "
              : ""}
            What it searches for goes to the provider, and the pages it reads come straight from their sites, public
            addresses only. Each search and page is in the activity log. Once an answer has read text from outside Lens,
            a search or a page you or a tool didn’t give it asks you first.
          </p>
          <More ctx={ctx} ids={["web.read_chars", "web.render"]} title="Reading pages">
            <F ctx={ctx} id="web.read_chars" />
            <F ctx={ctx} id="web.render" />
          </More>
        </>
      )}
    </div>
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
              saved={secret}
              updatedBy={d?.updated_by}
              updatedAt={d?.updated_at}
              value={key.value as string | undefined}
              onChange={(x) => key.onChange(x)}
            />
          )}
          <More
            ctx={ctx}
            ids={["decisions.base_url", "decisions.model", "decisions.timeout"]}
            title="Decision model server, model and timeout"
          >
            <div className="grid gap-3 sm:grid-cols-3">
              <F ctx={ctx} id="decisions.base_url" />
              <F ctx={ctx} id="decisions.model" />
              <F ctx={ctx} id="decisions.timeout" />
            </div>
          </More>
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
              saved={search?.values?.opensearch_password}
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
            saved={secret}
            updatedBy={ctx.view.embeddings?.updated_by}
            updatedAt={ctx.view.embeddings?.updated_at}
            value={key.value as string | undefined}
            onChange={(x) => key.onChange(x)}
          />
          <More
            ctx={ctx}
            ids={[
              "embeddings.api_key_env",
              "embeddings.min_similarity",
              "embeddings.neighbours",
              "embeddings.passage_chars",
              "embeddings.batch_size",
              "embeddings.query_prefix",
              "embeddings.document_prefix",
              "embeddings.timeout",
            ]}
            title="Matching, passages, prefixes and timeout"
          >
            <div className="grid gap-3 sm:grid-cols-2">
              <F ctx={ctx} id="embeddings.min_similarity" />
              <F ctx={ctx} id="embeddings.neighbours" />
              <F ctx={ctx} id="embeddings.passage_chars" />
              <F ctx={ctx} id="embeddings.batch_size" />
              <F ctx={ctx} id="embeddings.query_prefix" />
              <F ctx={ctx} id="embeddings.document_prefix" />
              <F ctx={ctx} id="embeddings.timeout" />
            </div>
            <F ctx={ctx} id="embeddings.api_key_env" hint="Used only when no key is stored above" />
          </More>
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
      <F ctx={ctx} id="embeddings.photos" />
      {s && (
        <div className="flex flex-col gap-2 rounded-md border border-blue-border bg-blue-surface px-3.5 py-3">
          <div className="flex flex-wrap items-center gap-2 text-[13px] font-semibold">
            <span className="flex-1">
              {!s.configured
                ? "Search by meaning is off"
                : s.current
                  ? `${s.indexed} of ${s.recordings} assets indexed with ${s.model}`
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
              ? `${s.passages} passages${s.dimension ? ` of ${s.dimension} dimensions` : ""}. New assets are indexed by their pipeline's Index for meaning step; the “${"Index for search by meaning"}” routine catches up every hour (Routines).`
              : "Turn it on and name a model to start. Assets are indexed by their pipelines and an hourly routine."}
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
      </div>
      <More
        ctx={ctx}
        ids={[
          "iiif.default_language",
          "iiif.provider.name",
          "iiif.provider.homepage",
          "iiif.provider.logo",
          "iiif.layers",
          "iiif.allowed_origins",
          "iiif.token_minutes",
        ]}
        title="Version, language, provider, layers, viewers and access tokens"
      >
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
          <F ctx={ctx} id="iiif.default_language" />
        </div>
        <div className="grid gap-3 sm:grid-cols-3">
          <F ctx={ctx} id="iiif.provider.name" />
          <F ctx={ctx} id="iiif.provider.homepage" label="Homepage" />
          <F ctx={ctx} id="iiif.provider.logo" label="Logo" />
        </div>
        <F ctx={ctx} id="iiif.layers" hint="Annotation layers are published only when an asset’s text is open." />
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
      </More>
      <div className="flex flex-col gap-2">
        <span className="text-[13px] font-bold leading-tight text-fg-strong">Access per namespace</span>
        <p className="text-[12.5px] text-fg-muted">
          The default for assets that don’t set their own. Saved into each namespace’s metadata profile.
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
      {SPEECH_PROVIDERS.map((p) => {
        const keySet = Boolean(((view?.values?.[`${p.id}_api_key`] ?? {}) as { set?: boolean }).set);
        return (
          <div key={p.id} className="border-t border-border pt-4">
            <More
              ctx={ctx}
              ids={[`speech.${p.id}_base_url`, `speech.${p.id}_model`]}
              open={keySet}
              title={
                <>
                  <h3 className="inline text-[15px] font-bold text-fg">{p.label}</h3>
                  <span className="font-normal text-fg-muted"> · {keySet ? "key set" : "not set up"}</span>
                </>
              }
            >
              <p className="m-0 text-[12.5px] leading-[1.45] text-fg-secondary">{p.about}</p>
              <div className="grid gap-3 sm:grid-cols-2">
                <F ctx={ctx} id={`speech.${p.id}_base_url`} />
                <F ctx={ctx} id={`speech.${p.id}_model`} />
                {!("readsOnly" in p) && (
                  <>
                    <F ctx={ctx} id={`speech.${p.id}_max_minutes`} />
                    <F ctx={ctx} id={`speech.${p.id}_max_mb`} />
                  </>
                )}
              </div>
              <SecretSetting
                key={view?.updated_at ?? "none"}
                label={`${p.label} API key`}
                isSet={Boolean(((view?.values?.[`${p.id}_api_key`] ?? {}) as { set?: boolean }).set)}
                saved={view?.values?.[`${p.id}_api_key`]}
                updatedBy={view?.updated_by}
                updatedAt={view?.updated_at}
                value={ctx.state(`speech.${p.id}_api_key`).value as string | undefined}
                onChange={(x) => ctx.state(`speech.${p.id}_api_key`).onChange(x)}
              />
              <SpeechTest provider={p.id} dirty={ctx.dirty} />
            </More>
          </div>
        );
      })}
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
      <More
        ctx={ctx}
        ids={["local_llm.context", "local_llm.threads", "local_llm.gpu_layers", "local_llm.port", "local_llm.host"]}
        title="Context, threads, GPU and address"
      >
        <div className="grid gap-3 sm:grid-cols-2">
          <F ctx={ctx} id="local_llm.context" />
          <F ctx={ctx} id="local_llm.threads" />
          <F ctx={ctx} id="local_llm.gpu_layers" />
          <F ctx={ctx} id="local_llm.port" />
          <F ctx={ctx} id="local_llm.host" />
        </div>
      </More>
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
