import type { Job } from "@/app/openapi-client/types.gen";
import { focusOwnsKey, keyToAction } from "@/components/player/keys";
import {
  duration,
  loopSteps,
  normalizeJob,
  orderedSteps,
  pageState,
  readyBefore,
  reprocessOptions,
  shortError,
  stepLabel,
  stepNotes,
  toggleStep,
  visualNotes,
  type JobInfo,
  type StepKey,
} from "@/components/recording/jobs";
import { failureImpact, sourceLabel, transcriptOrigin } from "@/components/recording/labels";

const job = (over: Partial<Job> & Record<string, unknown> = {}): JobInfo =>
  normalizeJob({
    id: 7,
    status: "running",
    steps: ["transcribe", "diarize", "analyze", "summarize", "report"],
    step_index: 0,
    next_step: "transcribe",
    created_at: "2026-09-30T10:00:00",
    ...over,
  } as Job);

describe("normalizeJob", () => {
  it("accepts step names and step specs", () => {
    const j = normalizeJob({
      id: 1,
      status: "queued",
      steps: ["analyze", { type: "llm", key: "meeting_notes", template: 1 }],
      step_index: 1,
    } as Job);
    expect(j.steps).toEqual([
      { type: "analyze" },
      {
        type: "llm",
        key: "meeting_notes",
        template: 1,
        name: undefined,
        version: undefined,
        model: undefined,
      },
    ]);
    expect(stepLabel(j.steps[1])).toBe("Meeting notes");
    expect(j.stepIndex).toBe(1);
  });
});

describe("step loop", () => {
  it("marks done, current and waiting steps while running", () => {
    const s = loopSteps(job({ step_index: 2, next_step: "analyze", started_at: "x" }));
    expect(s.map((x) => [x.label, x.state, x.tone])).toEqual([
      ["Transcribe", "done", "intent"],
      ["Diarize", "done", "red"],
      ["Analyze", "current", "green"],
      ["Summarize", "todo", "gate"],
      ["Report", "todo", "gate"],
    ]);
    expect(s[2].sub).toBe("running");
    expect(s[3].sub).toBe("waiting");
  });
  it("shows a failure with the steps after it skipped (R6)", () => {
    const s = loopSteps(
      job({
        status: "failed",
        step_index: 3,
        next_step: "summarize",
        error: "TimeoutError: provider timed out after 120 s",
      }),
    );
    expect(s.map((x) => x.state)).toEqual(["done", "done", "done", "failed", "skipped"]);
    expect(s[3].sub).toBe("provider timed out after 120 s");
  });
  it("fills everything when the job succeeded, with times from the log", () => {
    const j = job({
      status: "succeeded",
      step_index: 5,
      log: [
        "10:00:01 imported transcript: nothing to transcribe",
        "10:00:01 transcribe done in 0.0s",
        "10:00:02 diarize done in 0.1s",
        "10:00:40 analysed",
        "10:00:40 analyze done in 38.0s",
        "10:00:41 no LLM configured; skipped",
        "10:00:41 summarize done in 0.2s",
        "10:00:46 report done in 5.1s",
      ],
    });
    const notes = stepNotes(j);
    expect(notes.map((n) => [n.seconds, n.skipped])).toEqual([
      [0, true],
      [0.1, false],
      [38, false],
      [0.2, true],
      [5.1, false],
    ]);
    expect(notes[2].notes).toEqual(["analysed"]);
    const s = loopSteps(j, notes);
    expect(s.map((x) => x.sub)).toEqual(["skipped", "0.1 s", "38 s", "skipped", "5.1 s"]);
    expect(s.map((x) => x.state)).toEqual(["skipped", "done", "done", "skipped", "done"]);
  });
  it("takes notes and times from the run's records of its steps", () => {
    const j = job({
      status: "running",
      step_index: 2,
      steps: ["transcribe", "diarize", "analyze"],
      step_runs: [
        { outcome: "skipped", note: "an imported transcript has nothing to transcribe", seconds: 0 },
        { outcome: "done", note: "3 speakers", seconds: 4.5 },
        { outcome: "running", started_at: "2026-09-30T10:00:05+00:00" },
      ],
      log: ["10:00:00 not used when the run has records"],
    });
    expect(stepNotes(j)).toEqual([
      { seconds: 0, notes: ["an imported transcript has nothing to transcribe"], skipped: true },
      { seconds: 4.5, notes: ["3 speakers"], skipped: false },
      { seconds: null, notes: [], skipped: false },
    ]);
    expect(loopSteps(j, stepNotes(j)).map((x) => x.sub)).toEqual(["skipped", "4.5 s", "running"]);
  });
  it("reads any step's skip line in an older log", () => {
    const j = job({
      status: "succeeded",
      step_index: 2,
      steps: ["summarize", "report"],
      log: ["10:00:00 summarize skipped: no LLM is configured", "10:00:01 wrote it", "10:00:01 report done in 0.4s"],
    });
    expect(stepNotes(j).map((n) => [n.skipped, n.seconds, n.notes])).toEqual([
      [true, null, ["summarize skipped: no LLM is configured"]],
      [false, 0.4, ["wrote it"]],
    ]);
  });
  it("says why text on screen or objects weren't read, and when faces found none", () => {
    const j = job({
      status: "succeeded",
      step_index: 3,
      steps: ["shots", "ocr", "faces", "objects"],
      step_runs: [
        { outcome: "done", note: "3 shot(s), 3 sampled frame(s)", seconds: 0.5 },
        {
          outcome: "skipped",
          note: 'docTR isn\'t installed (pip install "lens[doctr]"; it brings PyTorch)',
          seconds: 0,
        },
        { outcome: "done", note: "no faces found", seconds: 0.2 },
        { outcome: "skipped", note: "object detection is off (video.object_engine)", seconds: 0 },
      ],
    });
    expect(visualNotes(j)).toEqual({
      ocrWhy: 'docTR isn\'t installed (pip install "lens[doctr]"; it brings PyTorch)',
      noFaces: true,
      objectsWhy: "object detection is off (video.object_engine)",
    });
    // from an older log's skip lines; read text needs no note
    const old = job({
      status: "succeeded",
      step_index: 1,
      steps: ["ocr"],
      log: ["10:00:00 ocr skipped: OCR is off (video.ocr_engine)"],
    });
    expect(visualNotes(old).ocrWhy).toBe("OCR is off (video.ocr_engine)");
    const read = job({
      status: "succeeded",
      step_index: 1,
      steps: ["ocr"],
      step_runs: [{ outcome: "done", note: "12 line(s)" }],
    });
    expect(visualNotes(read)).toEqual({ ocrWhy: null, noFaces: false, objectsWhy: null });
    expect(visualNotes(undefined)).toEqual({ ocrWhy: null, noFaces: false, objectsWhy: null });
  });
  it("says a queued job is waiting for a worker", () => {
    expect(loopSteps(job({ status: "queued", step_index: 2, started_at: "x" }))[2].sub).toBe("waiting for a worker");
    expect(loopSteps(job({ status: "queued", step_index: 0 }))[0].sub).toBe("queued");
  });
  it("formats durations and errors", () => {
    expect(duration(0.25)).toBe("0.3 s");
    expect(duration(72)).toBe("1 m 12 s");
    expect(duration(3725)).toBe("1 h 2 m");
    expect(shortError("RuntimeError: ffmpeg exited")).toBe("ffmpeg exited");
    expect(shortError(null)).toBeNull();
  });
});

describe("page state", () => {
  const imported = { status: "diarized", source: "transcript" };
  it("is processing while an audio recording is transcribed or diarized (R5)", () => {
    expect(pageState({ status: "new", source: "audio" }, [job()]).phase).toBe("processing");
    expect(
      pageState({ status: "transcribed", source: "audio" }, [job({ step_index: 1, next_step: "diarize" })]).phase,
    ).toBe("processing");
  });
  it("is analyzing for later steps, flagging a fresh import (R8)", () => {
    const s = pageState(imported, [job({ steps: ["analyze", "summarize", "report"], next_step: "analyze" })]);
    expect(s).toMatchObject({ phase: "analyzing", justImported: true });
    expect(
      pageState({ status: "analyzed", source: "transcript" }, [
        job({ steps: ["analyze", "report"], next_step: "analyze" }),
      ]).justImported,
    ).toBe(false);
  });
  it("treats transcribe on an imported transcript as analysis (it only skips)", () => {
    expect(pageState(imported, [job()]).phase).toBe("analyzing");
  });
  it("is failed when the newest job failed (R6), or the recording is in error", () => {
    const failed = job({
      id: 9,
      status: "failed",
      step_index: 3,
      next_step: "summarize",
      error: "timeout",
      created_at: "2026-09-30T11:00:00",
    });
    const s = pageState({ status: "analyzed", source: "audio" }, [job({ status: "succeeded", step_index: 5 }), failed]);
    expect(s).toMatchObject({
      phase: "failed",
      failedStep: "summarize",
      error: "timeout",
    });
    expect(s.job?.id).toBe(9);
    expect(pageState({ status: "error", error: "no audio stream" }, [])).toMatchObject({
      phase: "failed",
      failedStep: "transcribe",
      error: "no audio stream",
    });
  });
  it("is ready otherwise, even after an older failure", () => {
    const older = job({
      id: 1,
      status: "failed",
      created_at: "2026-09-29T10:00:00",
    });
    const newer = job({
      id: 2,
      status: "succeeded",
      step_index: 5,
      created_at: "2026-09-30T10:00:00",
    });
    expect(pageState({ status: "analyzed", source: "audio" }, [older, newer]).phase).toBe("ready");
    expect(pageState({ status: "analyzed" }, []).phase).toBe("ready");
  });
  it("lists what still works after a failure", () => {
    const j = job({ status: "failed", step_index: 3 });
    expect(readyBefore(j)).toEqual(["Transcribe", "Diarize", "Analyze"]);
    expect(failureImpact("summarize", readyBefore(j))).toBe(
      "Transcribe, Diarize and Analyze are ready; only the summary is missing.",
    );
    expect(failureImpact("transcribe", [])).toBe("Nothing after the import is ready yet.");
    // A template run on its own leaves the rest alone.
    expect(failureImpact("llm", [], true, "Meeting notes")).toBe(
      "Everything else is as it was; only the Meeting notes output is missing.",
    );
  });
});

describe("reprocess picker (R9)", () => {
  it("offers describing to videos, documents and images, not to audio", () => {
    const keys = (o: { video: boolean; hasAudio: boolean; paged?: boolean }) => reprocessOptions(o).map((x) => x.key);
    expect(keys({ video: true, hasAudio: true })).toContain("describe");
    expect(keys({ video: false, hasAudio: false, paged: true })).toContain("describe");
    expect(keys({ video: false, hasAudio: true })).not.toContain("describe");
    expect(toggleStep(new Set<StepKey>(), "shots", true, reprocessOptions({ video: true, hasAudio: true }))).toContain(
      "describe",
    ); // it reads the shots' keyframes
  });

  const audio = reprocessOptions({ video: false, hasAudio: true });
  const transcript = reprocessOptions({ video: false, hasAudio: false });
  it("offers the backend's steps; video steps only for videos", () => {
    expect(audio.map((o) => o.key)).toEqual(["transcribe", "diarize", "embed", "analyze", "summarize", "report"]);
    expect(reprocessOptions({ video: true, hasAudio: true }).map((o) => o.key)).toEqual([
      "transcribe",
      "diarize",
      "shots",
      "ocr",
      "faces",
      "objects",
      "describe",
      "embed",
      "analyze",
      "summarize",
      "report",
    ]);
    // a document's or an image's pages: faces, objects and descriptions, not shots or text on screen
    expect(reprocessOptions({ video: false, hasAudio: false, paged: true }).map((o) => o.key)).toEqual([
      "transcribe",
      "diarize",
      "faces",
      "objects",
      "describe",
      "embed",
      "analyze",
      "summarize",
      "report",
    ]);
  });
  it("disables Transcribe and Diarize for transcript-only recordings", () => {
    expect(transcript.filter((o) => o.disabled).map((o) => o.key)).toEqual(["transcribe", "diarize"]);
  });
  it("ticks dependents; unticking removes just that step", () => {
    let s = toggleStep(new Set<StepKey>(), "diarize", true, audio);
    expect(orderedSteps(s)).toEqual(["diarize", "analyze", "summarize", "report"]);
    s = toggleStep(s, "report", false, audio);
    expect(orderedSteps(s)).toEqual(["diarize", "analyze", "summarize"]);
    expect(orderedSteps(toggleStep(new Set<StepKey>(), "transcribe", true, transcript))).toEqual([]);
  });
});

describe("header helpers", () => {
  it("names the transcript's origin", () => {
    expect(transcriptOrigin("import:vtt")).toBe("from WebVTT");
    expect(transcriptOrigin("import:srt")).toBe("from SubRip");
    expect(transcriptOrigin("mlx-whisper")).toBe("mlx-whisper");
    expect(transcriptOrigin("import:paste")).toBe("from pasted text");
    expect(transcriptOrigin(null)).toBeNull();
  });
  it("labels the source: upload, storage, server file", () => {
    expect(sourceLabel({ path: "upload:ep12.srt" })).toMatchObject({
      text: "Uploaded · ep12.srt",
      remote: false,
    });
    expect(
      sourceLabel({
        path: "/x/y/ep12.m4a",
        remote: { source: 2, path: "media/eps/ep12.m4a" },
      }),
    ).toMatchObject({ text: "media/eps/ep12.m4a", remote: true });
    expect(sourceLabel({ path: "/srv/media/ep12.m4a" })).toMatchObject({
      text: "ep12.m4a",
      title: "/srv/media/ep12.m4a",
    });
    expect(sourceLabel({ path: "paste:779e6e" })).toMatchObject({
      text: "Pasted text",
      file: false,
    });
    expect(sourceLabel({ path: "/srv/media/ep12.m4a" })?.file).toBe(true);
    expect(sourceLabel({})).toBeNull();
  });
});

describe("player keys", () => {
  const el = (tag: string, attrs: Record<string, string> = {}, parent?: HTMLElement) => {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    (parent ?? document.body).appendChild(e);
    return e;
  };
  const body = document.body;
  it("maps the handoff's shortcuts", () => {
    expect(keyToAction({ key: " ", target: body })).toEqual({ type: "toggle" });
    expect(keyToAction({ key: "j", target: body })).toEqual({
      type: "seekBy",
      ms: -10000,
    });
    expect(keyToAction({ key: "L", target: body })).toEqual({
      type: "seekBy",
      ms: 10000,
    });
    expect(keyToAction({ key: "ArrowLeft", target: body })).toEqual({
      type: "seekBy",
      ms: -5000,
    });
    expect(keyToAction({ key: "ArrowDown", target: body })).toEqual({
      type: "turn",
      dir: 1,
    });
    expect(keyToAction({ key: "/", target: body })).toEqual({ type: "find" });
  });
  it("adds frame, shot and overlay keys for video only", () => {
    expect(keyToAction({ key: ".", target: body }, "video")).toEqual({
      type: "frame",
      dir: 1,
    });
    expect(keyToAction({ key: ".", target: body }, "audio")).toBeNull();
    expect(keyToAction({ key: "ArrowRight", shiftKey: true, target: body }, "video")).toEqual({ type: "shot", dir: 1 });
    expect(keyToAction({ key: "f", target: body }, "video")).toEqual({
      type: "overlay",
      which: "faces",
    });
  });
  it("leaves keys to text fields, widgets, buttons (Space) and modified presses", () => {
    expect(keyToAction({ key: "j", target: el("input") })).toBeNull();
    expect(keyToAction({ key: "ArrowLeft", target: el("button", { role: "tab" }) })).toBeNull();
    expect(
      keyToAction({
        key: "ArrowUp",
        target: el("span", {}, el("div", { role: "menu" })),
      }),
    ).toBeNull();
    expect(keyToAction({ key: " ", target: el("button") })).toBeNull();
    expect(keyToAction({ key: "j", target: el("button") })).toEqual({
      type: "seekBy",
      ms: -10000,
    });
    expect(keyToAction({ key: "k", metaKey: true, target: body })).toBeNull();
    expect(focusOwnsKey(el("div", { "data-player-keys": "off" }), "j")).toBe(true);
    // A focused slider (the waveform) keeps its arrows but Space still plays.
    const slider = el("div", { role: "slider" });
    expect(keyToAction({ key: "ArrowLeft", target: slider })).toBeNull();
    expect(keyToAction({ key: " ", target: slider })).toEqual({
      type: "toggle",
    });
    expect(keyToAction({ key: "j", target: el("button", { role: "tab" }) })).toEqual({ type: "seekBy", ms: -10000 });
  });
});
