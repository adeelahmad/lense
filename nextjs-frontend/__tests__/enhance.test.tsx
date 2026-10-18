import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Resources } from "@/app/openapi-client";
import type { Enhanced } from "@/app/openapi-client/types.gen";
import { EnhanceDetail } from "@/components/recording/enhance";
import { engineName, enhanceText } from "@/components/recording/enhance-model";
import { RecordingProvider, type RecordingCtx } from "@/components/recording/context";
import { DESCRIBE, NEEDS, moveStep } from "@/components/pipelines/pipeline-model";
import { FIELDS, SECTIONS, WORKER_STEPS } from "@/components/settings/model";
import { SETTINGS_GROUPS } from "@/components/settings/find";
import { STEP_LABEL } from "@/components/ui/loop";

jest.mock("@/app/openapi-client", () => ({ Resources: { enhanceRecording: jest.fn(), listSegmentEdits: jest.fn() } }));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
const push = jest.fn();
jest.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
const toast = jest.fn();
jest.mock("@/components/ui/toast", () => ({ useToast: () => toast }));

const MEASURED = { width: 240, height: 180, sharpness: 12, noise: 2, contrast: 40, brightness: 120 };
const MADE: Enhanced = {
  kind: "image",
  low: ["small", "blurry"],
  measured: MEASURED,
  enhanced: true,
  engine: "classic",
  scale: 4,
  width: 960,
  height: 720,
  forced: false,
  at: "2026-10-17T01:00:00Z",
  download: "/api/v1/recordings/9/enhanced?sig=abc",
};

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const ctx = (enhanced: Enhanced | null, canEdit = true) =>
  ({ id: 9, ns: "pods", rec: { id: 9, title: "Scan", enhanced }, paged: true, canEdit }) as unknown as RecordingCtx;
const wrap = (value: RecordingCtx, sound = false) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <RecordingProvider value={value}>
        <EnhanceDetail sound={sound} />
      </RecordingProvider>
    </QueryClientProvider>,
  );
};
const SOUND: Enhanced = {
  kind: "audio",
  low: ["noisy", "clipped"],
  measured: { duration: 12, snr_db: 9.5, level_db: -24.1, floor_db: -33.6, peak_db: 0, clipped: 0.004 },
  enhanced: true,
  engine: "ffmpeg",
  filters: ["adeclip", "highpass=f=70", "afftdn=nr=12:nf=-34"],
  at: "2026-10-17T01:00:00Z",
  download: "/api/v1/recordings/9/enhanced?sig=abc",
};

describe("enhance row text", () => {
  it("says what was found and what was made", () => {
    expect(enhanceText(null)).toBe("not run");
    expect(enhanceText(MADE)).toBe("Small, blurry · 240×180 to 960×720 by the classic engine");
    expect(enhanceText({ ...MADE, low: [], forced: true, engine: "realesr-general-x4v3" })).toBe(
      "On request · 240×180 to 960×720 by Real-ESRGAN",
    );
    expect(enhanceText({ ...MADE, low: ["dull", "small_faces"] })).toMatch(/^Low contrast, faces too small/);
    expect(enhanceText({ kind: "image", low: [], measured: MEASURED, enhanced: false })).toBe(
      "good enough as it is (240×180)",
    );
    expect(engineName(null)).toBeNull();
  });

  it("says what was heard in a recording", () => {
    expect(enhanceText(SOUND)).toBe("Noisy, clipped · signal to noise 9.5 dB, level -24.1 dBFS · by ffmpeg's filters");
    expect(enhanceText({ ...SOUND, low: [], engine: "webrtc", forced: true })).toMatch(/^On request · .* by WebRTC$/);
    expect(enhanceText({ ...SOUND, low: [], enhanced: false })).toBe(
      "good enough as it is (signal to noise 9.5 dB, level -24.1 dBFS)",
    );
  });
});

describe("EnhanceDetail", () => {
  beforeEach(() => jest.clearAllMocks());

  it("links the copy and enhances again on request", async () => {
    (Resources.enhanceRecording as jest.Mock).mockReturnValue(ok({ ok: true, job: 31 }));
    wrap(ctx(MADE));
    expect(screen.getByRole("link", { name: /Save the copy/ })).toHaveAttribute("href", MADE.download);
    fireEvent.click(screen.getByRole("button", { name: "Enhance again" }));
    await waitFor(() =>
      expect(Resources.enhanceRecording).toHaveBeenCalledWith(expect.objectContaining({ path: { rid: 9 } })),
    );
    await waitFor(() => expect(toast).toHaveBeenCalledWith(expect.objectContaining({ title: "Enhancing" })));
  });

  it("on a recording, enhancing transcribes it again, and says what that replaces", async () => {
    (Resources.listSegmentEdits as jest.Mock).mockReturnValue(
      ok([{ after: { text: "fixed" } }, { after: { speaker: 2 } }, { after: { text: "again" } }]),
    );
    (Resources.enhanceRecording as jest.Mock).mockReturnValue(ok({ ok: true, job: 32 }));
    wrap(ctx(SOUND), true);
    expect(await screen.findByText("Transcribing again replaces your 2 corrected lines.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Enhance and transcribe again" }));
    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(expect.objectContaining({ body: expect.stringMatching(/transcribed again/) })),
    );
  });

  it("offers nothing to save before a copy exists, and no button to viewers", () => {
    wrap(ctx(null, false));
    expect(screen.getByText("not run")).toBeInTheDocument();
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });
});

describe("enhance in the web app", () => {
  it("has a settings section with every backend key, off by default", () => {
    const keys = FIELDS.filter((f) => f.section === "enhance").map((f) => f.key);
    expect(keys).toEqual([
      "images",
      "image_engine",
      "only_faces",
      "min_side",
      "min_sharpness",
      "min_face_px",
      "max_scale",
      "audio",
      "audio_engine",
      "min_snr_db",
      "min_level_db",
    ]);
    expect(SECTIONS.some((s) => s.id === "enhance")).toBe(true);
    expect(SETTINGS_GROUPS.some((g) => g.sections.includes("enhance"))).toBe(true);
  });

  it("is a pipeline step that runs before read and transcribe", () => {
    expect(WORKER_STEPS[0]).toBe("enhance");
    expect(STEP_LABEL.enhance).toBe("Enhance");
    expect(DESCRIBE.enhance).toMatch(/Settings → Enhance/);
    expect(NEEDS.read).toContain("enhance");
    expect(NEEDS.transcribe).toContain("enhance");
    const steps = [{ type: "enhance" }, { type: "read" }];
    expect(moveStep(steps, 1, 0).error).toBeTruthy();
  });
});
