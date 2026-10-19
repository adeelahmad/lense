import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";

import type { Player, PublicRecording } from "@/app/openapi-client/types.gen";
import { CaptionTracks, captionMode } from "@/components/player/captions";
import { PublicRecordingView } from "@/components/public/recording-view";
import { normalizePlayer } from "@/components/recording/model";
import { TooltipProvider } from "@/components/ui/tooltip";

jest.mock("next-auth/react", () => ({ useSession: () => ({ data: null, status: "unauthenticated" }) }));
jest.mock("next/navigation", () => ({
  usePathname: () => "/explore/recordings/7",
  useSearchParams: () => new URLSearchParams(),
}));
const getPublicRecording = jest.fn();
jest.mock("@/app/openapi-client", () => ({ Public: { getPublicRecording: () => getPublicRecording() } }));
jest.mock("@/lib/api/browser", () => ({
  ApiError: class ApiError extends Error {},
  data: (x: unknown) => x,
  useApiClient: () => ({}),
}));

const TRACKS = [
  { kind: "captions" as const, label: "Transcript", language: "en", url: "/iiif/7/transcript.vtt?exp=1&sig=a" },
  { kind: "subtitles" as const, label: "Traduction", language: "fr", url: "/iiif/7/files/3.vtt?exp=1&sig=b" },
];

// jsdom has <track> but no TextTrack: give each one a mode to read back
const textTracks = new WeakMap<Element, { mode: TextTrackMode }>();
beforeAll(() => {
  Object.defineProperty(HTMLTrackElement.prototype, "track", {
    configurable: true,
    get(this: HTMLTrackElement) {
      if (!textTracks.has(this)) textTracks.set(this, { mode: "disabled" });
      return textTracks.get(this);
    },
  });
  Element.prototype.scrollIntoView = jest.fn();
});
const modes = (c: ParentNode = document) =>
  Array.from(c.querySelectorAll("track")).map((t) => (t as HTMLTrackElement).track.mode);

function setFullscreen(el: Element | null) {
  Object.defineProperty(document, "fullscreenElement", { configurable: true, get: () => el });
  act(() => {
    document.dispatchEvent(new Event("fullscreenchange"));
  });
}

describe("caption tracks", () => {
  afterEach(() => setFullscreen(null));

  it("are WebVTT tracks in the browser's captions menu, the transcript first and the default", () => {
    render(
      <video>
        <CaptionTracks tracks={TRACKS} on />
      </video>,
    );
    const [first, second] = Array.from(document.querySelectorAll("track"));
    expect(first).toHaveAttribute("kind", "captions");
    expect(first).toHaveAttribute("src", TRACKS[0].url);
    expect(first).toHaveAttribute("srclang", "en");
    expect(first).toHaveAttribute("label", "Transcript");
    expect(first).toHaveAttribute("default");
    expect(second).toHaveAttribute("kind", "subtitles");
    expect(second).not.toHaveAttribute("default");
    expect(modes()).toEqual(["showing", "disabled"]); // the others wait to be picked from the menu
  });

  it("stay undrawn while the page's own overlay shows the captions, and are drawn full screen", () => {
    const { container, rerender } = render(
      <video>
        <CaptionTracks tracks={TRACKS} on overlay />
      </video>,
    );
    expect(modes()[0]).toBe("hidden"); // never twice
    setFullscreen(container.querySelector("video"));
    expect(modes()[0]).toBe("showing"); // the overlay can't be seen there
    setFullscreen(null);
    expect(modes()[0]).toBe("hidden");
    rerender(
      <video>
        <CaptionTracks tracks={TRACKS} on={false} overlay />
      </video>,
    );
    expect(modes()[0]).toBe("disabled");
    expect(document.querySelector("track")).not.toHaveAttribute("default");
  });

  it("say how the browser treats them", () => {
    expect(captionMode(true, false)).toBe("showing");
    expect(captionMode(true, true)).toBe("hidden");
    expect(captionMode(false, false)).toBe("disabled");
  });

  it("come with the player's data", () => {
    const m = normalizePlayer({ id: 7, captions: [...TRACKS, { kind: "captions", label: "x" }] } as unknown as Player);
    expect(m.captions).toEqual(TRACKS);
    expect(normalizePlayer({ id: 7 } as unknown as Player).captions).toEqual([]);
  });
});

const REC = {
  id: 7,
  title: "Harbour at dawn",
  view: "public",
  access: "public",
  media_kind: "video",
  open: ["media", "transcript"],
  closed: [],
  member: false,
  featured: false,
  duration_ms: 4000,
  media: { url: "/api/v1/recordings/7/audio?sig=x", kind: "video", poster: null, envelope: null },
  transcript: { speakers: [], segments: [{ t0: 0, t1: 2000, s: null, text: "Zanzibar at dawn." }], downloads: [] },
  chapters: [],
  files: [],
  files_closed: 0,
  captions: TRACKS,
} as unknown as PublicRecording;

describe("a video's public page", () => {
  it("plays its text as captions the browser draws once the visitor turns them on", async () => {
    getPublicRecording.mockResolvedValue(REC);
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <TooltipProvider>
          <PublicRecordingView id={7} />
        </TooltipProvider>
      </QueryClientProvider>,
    );
    const toggle = await screen.findByRole("button", { name: "Captions" });
    const video = screen.getByLabelText("Video: Harbour at dawn");
    expect(video.querySelectorAll("track")).toHaveLength(2);
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    expect(modes(video)[0]).toBe("disabled");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    expect(modes(video)).toEqual(["showing", "disabled"]);
  });

  it("offers no captions without tracks", async () => {
    getPublicRecording.mockResolvedValue({ ...REC, captions: [] });
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <TooltipProvider>
          <PublicRecordingView id={7} />
        </TooltipProvider>
      </QueryClientProvider>,
    );
    await screen.findByLabelText("Video: Harbour at dawn");
    expect(screen.queryByRole("button", { name: "Captions" })).not.toBeInTheDocument();
    expect(document.querySelector("track")).toBeNull();
  });
});
