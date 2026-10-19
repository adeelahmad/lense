"use client";

import { useEffect, useRef, useState, type RefObject } from "react";

import type { CaptionTrack } from "@/app/openapi-client/types.gen";

/**
 * How the browser treats the first caption track (docs/video.md#captions): drawn by the browser when captions are on
 * and nothing of the page draws them, loaded but not drawn while the page's own overlay does (so they don't show
 * twice), and off when captions are off.
 */
export function captionMode(on: boolean, overlay: boolean): TextTrackMode {
  if (!on) return "disabled";
  return overlay ? "hidden" : "showing";
}

type WebkitVideo = HTMLVideoElement & { webkitDisplayingFullscreen?: boolean };

/** Whether the <video> holding this track is shown where the page can't draw over it: full screen on its own, or
 * picture in picture. */
function useVideoAlone(track: RefObject<HTMLTrackElement | null>, present: boolean): boolean {
  const [alone, setAlone] = useState(false);
  useEffect(() => {
    const video = track.current?.parentElement as WebkitVideo | null | undefined;
    if (!video) return;
    const check = () =>
      setAlone(
        document.fullscreenElement === video ||
          document.pictureInPictureElement === video ||
          Boolean(video.webkitDisplayingFullscreen),
      );
    const onDoc = ["fullscreenchange", "webkitfullscreenchange"];
    const onVideo = ["enterpictureinpicture", "leavepictureinpicture", "webkitbeginfullscreen", "webkitendfullscreen"];
    onDoc.forEach((e) => document.addEventListener(e, check));
    onVideo.forEach((e) => video.addEventListener(e, check));
    return () => {
      onDoc.forEach((e) => document.removeEventListener(e, check));
      onVideo.forEach((e) => video.removeEventListener(e, check));
    };
  }, [track, present]);
  return alone;
}

/**
 * The asset's text as WebVTT <track>s, to put inside its <video>: the transcript first, then supplementary captions
 * and translations, all in the browser's own captions menu, full screen and picture in picture. `on`: captions are
 * shown (the first track is the default). `overlay`: the page draws the captions over the video itself, so the
 * browser draws them only where that overlay can't be seen (full screen, picture in picture).
 */
export function CaptionTracks({
  tracks,
  on,
  overlay = false,
}: {
  tracks: CaptionTrack[];
  on: boolean;
  overlay?: boolean;
}) {
  const first = useRef<HTMLTrackElement>(null);
  const src = tracks[0]?.url;
  const alone = useVideoAlone(first, Boolean(src));
  useEffect(() => {
    const t = first.current?.track;
    if (t) t.mode = captionMode(on, overlay && !alone);
  }, [on, overlay, alone, src]);
  return (
    <>
      {tracks.map((t, i) => (
        <track
          // by place, not link: a re-signed link reloads the same track and keeps its mode
          key={i}
          ref={i === 0 ? first : undefined}
          kind={t.kind}
          src={t.url}
          label={t.label}
          srcLang={t.language ?? undefined}
          default={on && i === 0}
        />
      ))}
    </>
  );
}
