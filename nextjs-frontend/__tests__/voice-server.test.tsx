import { act, renderHook, waitFor } from "@testing-library/react";

import { Voice as VoiceApi } from "@/app/openapi-client";
import { PAUSE_MS, useVoice, WAIT_MS } from "@/lib/voice";

jest.mock("@/app/openapi-client", () => ({
  Voice: { voiceInfo: jest.fn(), transcribeClip: jest.fn(), speakText: jest.fn() },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const m = (f: unknown) => f as jest.Mock;
let loudness = 0.001;
const stopped = jest.fn();

class FakeRecorder {
  static isTypeSupported = (t: string) => t === "audio/webm;codecs=opus";
  state = "inactive";
  mimeType = "audio/webm;codecs=opus";
  ondataavailable: ((e: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  start() {
    this.state = "recording";
    this.ondataavailable?.({ data: new Blob(["voice"]) });
  }
  stop() {
    this.state = "inactive";
    this.onstop?.();
  }
}

class FakeContext {
  createAnalyser() {
    return { fftSize: 0, getFloatTimeDomainData: (b: Float32Array) => b.fill(loudness) };
  }
  createMediaStreamSource() {
    return { connect: () => undefined };
  }
  close() {
    return Promise.resolve();
  }
}

class FakeAudio {
  onended: (() => void) | null = null;
  onerror: (() => void) | null = null;
  static played: string[] = [];
  constructor(public src: string) {}
  play() {
    FakeAudio.played.push(this.src);
    setTimeout(() => this.onended?.(), 10);
    return Promise.resolve();
  }
  pause() {}
}

beforeEach(() => {
  jest.useFakeTimers();
  loudness = 0.001;
  const w = window as unknown as Record<string, unknown>;
  w.MediaRecorder = FakeRecorder;
  w.AudioContext = FakeContext;
  w.Audio = FakeAudio;
  delete w.SpeechRecognition;
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia: jest.fn(() => Promise.resolve({ getTracks: () => [{ stop: stopped }] })) },
  });
  URL.createObjectURL = jest.fn(() => "blob:answer");
  URL.revokeObjectURL = jest.fn();
  m(VoiceApi.voiceInfo).mockReturnValue(ok({ transcribe: true, engine: "sensevoice", speak: true }));
  m(VoiceApi.transcribeClip).mockReturnValue(ok({ text: "book a call for Friday ", seconds: 2 }));
  m(VoiceApi.speakText).mockResolvedValue({ data: new Blob(["mp3"]), response: { status: 200 } });
});
afterEach(() => jest.useRealTimers());

async function flush() {
  for (let i = 0; i < 10; i++) await Promise.resolve();
}

/** Time passing in steps, letting the promises in between settle. */
async function tick(ms: number) {
  await act(async () => {
    await flush();
    for (let t = 0; t < ms; t += 100) {
      jest.advanceTimersByTime(100);
      await flush();
    }
  });
}

test("the server hears what was said, up to a pause, and shows it while talking", async () => {
  const { result } = renderHook(() => useVoice());
  await waitFor(() => expect(result.current.supported).toBe(true)); // no browser recognition: the server's
  let said: string | null = null;
  act(() => {
    void result.current.listen().then((t) => (said = t));
  });
  await tick(400); // the room's noise first
  expect(result.current.listening).toBe(true);
  loudness = 0.2;
  await tick(1700); // talking: what's been heard so far comes back
  await waitFor(() => expect(result.current.heard).toBe("book a call for Friday "));
  loudness = 0.001;
  await tick(PAUSE_MS + 200); // a pause ends it
  await waitFor(() => expect(said).toBe("book a call for Friday"));
  expect(result.current.listening).toBe(false);
  expect(stopped).toHaveBeenCalled(); // the mic is let go
  const body = m(VoiceApi.transcribeClip).mock.calls.at(-1)[0].body as Blob;
  expect(body.type).toBe("audio/webm;codecs=opus");
});

test("nothing said: it gives up quietly without asking the server", async () => {
  const { result } = renderHook(() => useVoice());
  await waitFor(() => expect(result.current.supported).toBe(true));
  m(VoiceApi.transcribeClip).mockClear();
  let said: string | null = null;
  act(() => {
    void result.current.listen().then((t) => (said = t));
  });
  await tick(WAIT_MS + 500);
  await waitFor(() => expect(said).toBe(""));
  expect(VoiceApi.transcribeClip).not.toHaveBeenCalled();
});

test("answers are read by the server's speech model, without citation marks", async () => {
  const { result } = renderHook(() => useVoice());
  await waitFor(() => expect(result.current.supported).toBe(true));
  let done = false;
  act(() => {
    void result.current.speak("It leaves on **Friday** [1].").then(() => (done = true));
  });
  await waitFor(() => expect(FakeAudio.played).toEqual(["blob:answer"]));
  expect(m(VoiceApi.speakText).mock.calls[0][0].body).toEqual({ text: "It leaves on Friday." });
  await tick(20);
  await waitFor(() => expect(done).toBe(true));
});
