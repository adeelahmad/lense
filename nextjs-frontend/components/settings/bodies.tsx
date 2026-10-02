"use client";

import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { PlugZap, RefreshCw, Terminal } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { Admin, Analytics, Metadata } from "@/app/openapi-client";
import { AllTokens } from "@/components/account/all-tokens";
import { ACCESS } from "@/components/iiif/metadata-model";
import { RIGHTS } from "@/components/iiif/rights";
import { SecretSetting, SettingField, ZoneBar, type FieldState } from "@/components/settings/fields";
import { AI_TOOLS, type FieldSpec, type SectionId, type SettingsView } from "@/components/settings/model";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Select, Switch } from "@/components/ui/field";
import { Skeleton } from "@/components/ui/states";
import { data, useApiClient } from "@/lib/api/browser";
import { useArchive } from "@/lib/hooks/session";
import { count, shortDate } from "@/lib/format";
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
            By channel suits call recorders that put each side on its own channel. Off keeps one speaker per recording.
            Leave min and max empty to let it decide.
          </p>
        </>
      );
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
          <F
            ctx={ctx}
            id="analysis.gazetteer"
            label={
              <>
                Custom vocabulary <span className="font-normal text-fg-muted">· one per line, Name | TYPE</span>
              </>
            }
            hint="Types: PERSON, ORG, PRODUCT, PLACE, EVENT, WORK, TERM. A line without a type is a topic."
          />
        </>
      );
    case "llm":
      return <LlmBody ctx={ctx} />;
    case "ai":
      return <AiBody ctx={ctx} />;
    case "search":
      return (
        <>
          <F ctx={ctx} id="search.stemming" />
          <Reindex />
          <F ctx={ctx} id="search.semantic" />
          <F ctx={ctx} id="search.semantic_weight" />
          <p className="text-[12.5px] leading-[1.45] text-fg-secondary">
            Model:{" "}
            <code className="break-all font-mono text-fg-secondary">
              {ctx.view.bootstrap?.semantic_model?.model ?? "—"}
            </code>
            {ctx.view.bootstrap?.semantic_model?.reason ? ` · ${ctx.view.bootstrap.semantic_model.reason}` : " · ready"}
            . Another model (a multilingual one, say) is set at startup: search.semantic_model.
          </p>
        </>
      );
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
    case "tokens":
      return (
        <>
          <div className="grid gap-3 sm:grid-cols-2">
            <F ctx={ctx} id="tokens.default_days" />
            <F ctx={ctx} id="tokens.max_days" />
          </div>
          <F ctx={ctx} id="tokens.never_expire" />
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
    case "analytics":
      return (
        <>
          <F ctx={ctx} id="analytics.retention_days" />
          <AnalyticsKept />
        </>
      );
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

function AiBody({ ctx }: { ctx: BodyCtx }) {
  const tools = ctx.state("ai.disabled_tools");
  const off = (tools.value as string[]) ?? [];
  const enabled = Boolean(ctx.form["ai.tools"]);
  return (
    <>
      <p className="text-[13px] leading-normal text-fg-secondary">
        The assistant uses the model set in LLM provider; it needs one that supports tool calls.
      </p>
      <F ctx={ctx} id="ai.tools" />
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

/** What analytics keep, and purging it (docs/analytics.md). */
function AnalyticsKept() {
  const client = useApiClient();
  const qc = useQueryClient();
  const [confirm, setConfirm] = useState(false);
  const status = useQuery({
    queryKey: ["analytics", "status"],
    queryFn: () => data(Analytics.analyticsStatus({ client })),
    staleTime: 0,
  });
  const purge = useMutation({
    mutationFn: (everything: boolean) => data(Analytics.purgeAnalytics({ client, body: { everything } })),
    onSuccess: () => {
      setConfirm(false);
      void qc.invalidateQueries({ queryKey: ["analytics"] });
    },
  });
  const s = status.data;
  return (
    <div className="flex flex-col gap-2.5 rounded-md border border-border px-3.5 py-3">
      <span className="text-[13px] font-bold text-fg-strong">What’s kept</span>
      <span className="text-[13px] leading-[1.45] text-fg-secondary">
        {status.isPending
          ? "Counting…"
          : status.isError
            ? `Couldn’t tell: ${status.error.message}`
            : s && s.events
              ? `${count(s.events)} ${s.events === 1 ? "action" : "actions"} with their accounts, the oldest from ${shortDate(s.oldest)}; ${count(s.days)} daily counts.`
              : `No actions with accounts${s?.days ? `; ${count(s.days)} daily counts` : ""}.`}{" "}
        Each action is the account, the resource and the time: no addresses, no browsers. Workers delete what’s past its
        days every hour.
      </span>
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="secondary" disabled={purge.isPending} onClick={() => purge.mutate(false)}>
          Purge what’s past its days now
        </Button>
        {confirm ? (
          <>
            <Button size="sm" variant="ghost" onClick={() => setConfirm(false)}>
              Keep
            </Button>
            <Button size="sm" variant="danger" disabled={purge.isPending} onClick={() => purge.mutate(true)}>
              Delete all of it
            </Button>
          </>
        ) : (
          <Button size="sm" variant="danger-ghost" onClick={() => setConfirm(true)}>
            Delete everything…
          </Button>
        )}
        {purge.isSuccess && (
          <span role="status" className="text-[12.5px] text-fg-secondary">
            {count(purge.data.deleted)} deleted.
          </span>
        )}
        {purge.isError && <span className="text-[12.5px] text-red-dark">{purge.error.message}</span>}
      </div>
    </div>
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
          : "Run it after changing stemming."}
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
