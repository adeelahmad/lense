import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Composer } from "@/components/chat/composer";
import { AssistantHome, assistantChatHref } from "@/components/home/assistant-home";
import { homeMode, linkedMode } from "@/components/home/home-page";
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

  it("opens the overview from the bell, whatever mode was picked", () => {
    expect(linkedMode("#attention")).toBe("overview");
    expect(linkedMode("")).toBeNull();
  });

  it("starts assistant chats over everything, typed or spoken", () => {
    expect(assistantChatHref()).toBe("/chat?global=1");
    expect(assistantChatHref({ q: "hi there" })).toBe("/chat?global=1&q=hi+there");
    expect(assistantChatHref({ voice: true })).toBe("/chat?global=1&voice=1");
  });
});

describe("assistant mode", () => {
  it("asks the typed question in a chat on Enter", () => {
    render(<AssistantHome />);
    const field = screen.getByLabelText("Ask anything");
    fireEvent.pointerDown(field);
    fireEvent.change(field, { target: { value: "who called on Monday?" } });
    expect(push).not.toHaveBeenCalled(); // typing stays here, so no keystroke is lost on the way
    fireEvent.keyDown(field, { key: "Enter" });
    expect(push).toHaveBeenLastCalledWith("/chat?global=1&q=who+called+on+Monday%3F&send=1");
  });

  it("opens an empty chat on Enter in an empty field", () => {
    render(<AssistantHome />);
    fireEvent.keyDown(screen.getByLabelText("Ask anything"), { key: "Enter" });
    expect(push).toHaveBeenLastCalledWith("/chat?global=1");
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

describe("a chat over everything", () => {
  it("hears which namespace it was narrowed to", () => {
    const { applyEvent, newTurn } = jest.requireActual("@/components/chat/stream");
    const t = applyEvent(newTurn("q"), { event: "scoped", data: '{"namespaces":["calls"],"confidence":0.9}' });
    expect(t.scoped).toEqual(["calls"]);
    expect(newTurn("q").scoped).toBeNull();
  });
});

describe("namespaces offered to pick from", () => {
  const items = [
    { name: "calls", new: false },
    { name: "family-trips", new: true },
  ];

  it("hears the offer", () => {
    const { applyEvent, newTurn } = jest.requireActual("@/components/chat/stream");
    const t = applyEvent(newTurn("q"), {
      event: "suggested",
      data: JSON.stringify({ namespaces: [...items, { bad: 1 }] }),
    });
    expect(t.suggested).toEqual(items);
  });

  it("takes a spoken pick, and only one that names an offer", () => {
    const { spokenPick } = jest.requireActual("@/components/chat/namespace-offer");
    expect(spokenPick("Use family trips please", items)).toEqual(items[1]);
    expect(spokenPick("calls", items)).toEqual(items[0]);
    expect(spokenPick("what about the recalls?", items)).toBeNull();
  });

  it("shows existing and new ones to tap, and a way to keep everything", () => {
    const { NamespaceOffer } = jest.requireActual("@/components/chat/namespace-offer");
    const onPick = jest.fn();
    const onDismiss = jest.fn();
    render(<NamespaceOffer items={items} busy={false} onPick={onPick} onDismiss={onDismiss} />);
    fireEvent.click(screen.getByRole("button", { name: "New: family-trips" }));
    expect(onPick).toHaveBeenCalledWith(items[1]);
    fireEvent.click(screen.getByRole("button", { name: "Keep everything" }));
    expect(onDismiss).toHaveBeenCalled();
  });
});
