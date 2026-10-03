import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Composer } from "@/components/chat/composer";
import { AssistantHome, assistantChatHref } from "@/components/home/assistant-home";
import { homeMode } from "@/components/home/home-page";
import { speakable, useVoice, voiceError, VoiceError } from "@/lib/voice";

const push = jest.fn();
const toast = jest.fn();
jest.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("@/lib/hooks/session", () => ({ useArchive: () => ({ me: { user: { name: "Adeel Ahmad" } } }) }));
jest.mock("@/components/ui/toast", () => ({ useToast: () => toast }));
jest.mock("@/components/import/pending", () => ({ chooseFiles: jest.fn(() => Promise.resolve([])) }));
jest.mock("@/components/chat/data", () => ({
  useChats: () => ({
    data: [
      { id: 7, title: "Budget talk", kind: "chat" },
      { id: 8, title: "Help me set up Lens", kind: "setup" },
    ],
  }),
}));

/** A stand-in for the browser's SpeechRecognition that hears `said` (or fails with `error`). */
function fakeRecognition(said: string, error?: string) {
  return class {
    lang = "";
    interimResults = false;
    continuous = false;
    onresult: ((e: unknown) => void) | null = null;
    onerror: ((e: { error: string }) => void) | null = null;
    onend: (() => void) | null = null;
    start() {
      setTimeout(() => {
        if (error) this.onerror?.({ error });
        else if (said)
          this.onresult?.({ resultIndex: 0, results: [Object.assign([{ transcript: said }], { isFinal: true })] });
        this.onend?.();
      }, 0);
    }
    stop() {}
    abort() {}
  };
}

const w = window as unknown as Record<string, unknown>;
afterEach(() => {
  delete w.SpeechRecognition;
  push.mockReset();
  toast.mockReset();
});

describe("which home a visit opens", () => {
  it("opens the assistant once the archive has content, unless you picked otherwise", () => {
    expect(homeMode(null, true)).toBe("assistant");
    expect(homeMode(null, false)).toBe("overview");
    expect(homeMode("overview", true)).toBe("overview");
    expect(homeMode("assistant", false)).toBe("assistant");
  });

  it("starts assistant chats over everything, typed or spoken", () => {
    expect(assistantChatHref()).toBe("/chat?global=1");
    expect(assistantChatHref({ q: "hi there" })).toBe("/chat?global=1&q=hi+there");
    expect(assistantChatHref({ voice: true })).toBe("/chat?global=1&voice=1");
  });
});

describe("assistant mode", () => {
  it("turns into a chat as soon as the field is touched or typed in", () => {
    render(<AssistantHome />);
    const field = screen.getByLabelText("Ask anything");
    fireEvent.pointerDown(field);
    expect(push).toHaveBeenLastCalledWith("/chat?global=1");
    fireEvent.change(field, { target: { value: "w" } });
    expect(push).toHaveBeenLastCalledWith("/chat?global=1&q=w");
  });

  it("lists earlier conversations to switch to, but not setup chats", () => {
    render(<AssistantHome />);
    expect(screen.getByRole("link", { name: "Budget talk" })).toHaveAttribute("href", "/chat/7");
    expect(screen.queryByText("Help me set up Lens")).not.toBeInTheDocument();
  });

  it("starts a voice chat from the mic", async () => {
    w.SpeechRecognition = fakeRecognition("hello");
    render(<AssistantHome />);
    fireEvent.click(screen.getByRole("button", { name: "Talk" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/chat?global=1&voice=1"));
  });

  it("falls back to typing where the browser can't listen", async () => {
    render(<AssistantHome />);
    fireEvent.click(screen.getByRole("button", { name: "Talk" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/chat?global=1"));
    expect(toast).toHaveBeenCalledWith(expect.objectContaining({ title: "This browser can’t listen yet" }));
  });
});

describe("voice", () => {
  it("hears what was said", async () => {
    w.SpeechRecognition = fakeRecognition("what came up this week");
    const { result } = renderHook(() => useVoice());
    expect(result.current.supported).toBe(true);
    let said = "";
    await act(async () => {
      said = await result.current.listen();
    });
    expect(said).toBe("what came up this week");
  });

  it("reports a blocked microphone, and stays quiet about silence", async () => {
    w.SpeechRecognition = fakeRecognition("", "not-allowed");
    const { result } = renderHook(() => useVoice());
    await act(async () => {
      await expect(result.current.listen()).rejects.toBeInstanceOf(VoiceError);
    });
    w.SpeechRecognition = fakeRecognition("", "no-speech");
    let said = "x";
    await act(async () => {
      said = await result.current.listen();
    });
    expect(said).toBe("");
    expect(voiceError("no-speech")).toBeNull();
  });

  it("reads answers aloud without citation marks or markdown", () => {
    expect(speakable("**Yes** [1], the budget [2, 3] was cut. See [notes](/r/1).")).toBe(
      "Yes, the budget was cut. See notes.",
    );
  });

  it("has a mic in the chat composer that toggles voice mode", () => {
    const onToggle = jest.fn();
    render(
      <Composer
        value=""
        onChange={() => {}}
        onSend={() => {}}
        busy={false}
        placeholder="Ask"
        voice={{ on: false, onToggle }}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Talk" }));
    expect(onToggle).toHaveBeenCalled();
  });
});
