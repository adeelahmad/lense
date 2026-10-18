import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Voice as VoiceApi } from "@/app/openapi-client";
import { Answer } from "@/components/chat/answer";
import { UserBubble } from "@/components/chat/user-bubble";
import { TooltipProvider } from "@/components/ui/tooltip";
import { forgetVoice, pieces, PIECE_CHARS, stopReading } from "@/lib/voice";

jest.mock("@/app/openapi-client", () => ({
  Voice: { voiceInfo: jest.fn(), speakText: jest.fn() },
  Chats: { checkMessage: jest.fn() },
  Search: {},
}));
jest.mock("@/lib/api/browser", () => ({
  useApiClient: () => ({}),
  data: (p: Promise<{ data: unknown }>) => p.then((r) => r.data),
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("@/lib/hooks/session", () => ({
  useArchive: () => ({ can: () => true, namespaces: [], roleIn: () => "editor" }),
}));
jest.mock("@/components/chat/attachments", () => ({ SentFiles: () => null }));

const m = (f: unknown) => f as jest.Mock;

class FakeAudio {
  static playing: FakeAudio[] = [];
  static played: string[] = [];
  onended: (() => void) | null = null;
  onerror: (() => void) | null = null;
  paused = false;
  constructor(public src: string) {}
  play() {
    FakeAudio.played.push(this.src);
    FakeAudio.playing.push(this);
    return Promise.resolve();
  }
  pause() {
    this.paused = true;
  }
  /** It plays to the end. */
  end() {
    this.onended?.();
  }
}

let clipN = 0;
beforeAll(() => {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  })) as unknown as typeof window.matchMedia;
});
beforeEach(() => {
  jest.clearAllMocks();
  forgetVoice();
  stopReading();
  FakeAudio.playing = [];
  FakeAudio.played = [];
  (window as unknown as Record<string, unknown>).Audio = FakeAudio;
  URL.createObjectURL = jest.fn(() => `blob:${++clipN}`);
  URL.revokeObjectURL = jest.fn();
  m(VoiceApi.voiceInfo).mockResolvedValue({ data: { transcribe: false, speak: true }, response: { status: 200 } });
  m(VoiceApi.speakText).mockImplementation(() =>
    Promise.resolve({ data: new Blob(["mp3"]), response: { status: 200 } }),
  );
});

function show(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>{ui}</TooltipProvider>
    </QueryClientProvider>,
  );
}

const answer = (text: string, messageId = 7) => (
  <Answer
    chatId={1}
    messageId={messageId}
    question="When does it leave?"
    text={text}
    passages={[]}
    status="done"
    scope={{}}
    model="m"
    hover={null}
    onHover={() => {}}
    onPreview={() => {}}
  />
);

describe("reading a message aloud", () => {
  it("cuts long text after sentences, under every provider's limit", () => {
    const sentence = "The ship leaves on Friday at noon. ";
    const long = sentence.repeat(100);
    const out = pieces(long);
    expect(out.length).toBeGreaterThan(1);
    expect(out.every((p) => p.length <= PIECE_CHARS && p.endsWith("."))).toBe(true);
    expect(out.join(" ")).toBe(long.trim());
    expect(pieces("one two three four", 9)).toEqual(["one two", "three", "four"]);
    expect(pieces("  ")).toEqual([]);
  });

  it("has nothing to read in an answer that is only code", () => {
    show(answer("```\nls -la\n```"));
    expect(screen.queryByRole("button", { name: "Read aloud" })).not.toBeInTheDocument();
  });

  it("plays an answer by the speech model in one click, stops on the next, and keeps its audio", async () => {
    show(answer("It leaves on **Friday** [1]."));
    fireEvent.click(screen.getByRole("button", { name: "Read aloud" }));
    await waitFor(() => expect(FakeAudio.played).toHaveLength(1));
    expect(m(VoiceApi.speakText).mock.calls[0][0].body).toEqual({ text: "It leaves on Friday." });
    const stop = screen.getByRole("button", { name: "Stop reading aloud" });
    expect(stop).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(stop);
    expect(FakeAudio.playing[0].paused).toBe(true);
    expect(screen.getByRole("button", { name: "Read aloud" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Read aloud" })); // again: the kept audio, no second call
    await waitFor(() => expect(FakeAudio.played).toHaveLength(2));
    expect(VoiceApi.speakText).toHaveBeenCalledTimes(1);
    act(() => FakeAudio.playing[1].end());
    await waitFor(() => expect(screen.getByRole("button", { name: "Read aloud" })).toBeInTheDocument());
  });

  it("reads one message at a time: starting another stops the first", async () => {
    show(
      <>
        <UserBubble text="When does the ship leave?" />
        {answer("It leaves on Friday.")}
      </>,
    );
    const [question, reply] = screen.getAllByRole("button", { name: "Read aloud" });
    fireEvent.click(reply);
    await waitFor(() => expect(FakeAudio.played).toHaveLength(1));
    fireEvent.click(question);
    await waitFor(() => expect(FakeAudio.played).toHaveLength(2));
    expect(FakeAudio.playing[0].paused).toBe(true);
    expect(m(VoiceApi.speakText).mock.calls[1][0].body).toEqual({ text: "When does the ship leave?" });
    expect(screen.getAllByRole("button", { name: "Stop reading aloud" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "Read aloud" })).toHaveLength(1);
  });

  it("stops when the message leaves the page", async () => {
    const { unmount } = show(answer("It leaves on Friday."));
    fireEvent.click(screen.getByRole("button", { name: "Read aloud" }));
    await waitFor(() => expect(FakeAudio.played).toHaveLength(1));
    unmount();
    expect(FakeAudio.playing[0].paused).toBe(true);
  });

  it("reads a long answer piece by piece, asking for the next while one plays", async () => {
    show(answer(Array.from({ length: 120 }, (_, i) => `The ship number ${i} leaves on Friday at noon.`).join(" ")));
    fireEvent.click(screen.getByRole("button", { name: "Read aloud" }));
    await waitFor(() => expect(FakeAudio.played).toHaveLength(1));
    await waitFor(() => expect(VoiceApi.speakText).toHaveBeenCalledTimes(2)); // the next piece, while this one plays
    act(() => FakeAudio.playing[0].end());
    await waitFor(() => expect(FakeAudio.played).toHaveLength(2));
    for (const [{ body }] of m(VoiceApi.speakText).mock.calls)
      expect(body.text.length).toBeLessThanOrEqual(PIECE_CHARS);
  });

  it("is read by the browser when the server has no speech model", async () => {
    m(VoiceApi.voiceInfo).mockResolvedValue({ data: { transcribe: false, speak: false }, response: { status: 200 } });
    const said: string[] = [];
    const speak = jest.fn((u: { text: string; onend?: () => void }) => said.push(u.text));
    Object.assign(window, { speechSynthesis: { speak, cancel: jest.fn() } });
    (window as unknown as Record<string, unknown>).SpeechSynthesisUtterance = class {
      lang = "";
      onend: (() => void) | null = null;
      onerror: (() => void) | null = null;
      constructor(public text: string) {}
    };
    show(answer("It leaves on Friday [1]."));
    fireEvent.click(screen.getByRole("button", { name: "Read aloud" }));
    await waitFor(() => expect(speak).toHaveBeenCalled());
    expect(said).toEqual(["It leaves on Friday."]);
  });
});
