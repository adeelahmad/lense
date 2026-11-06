/** The raw voice API accepts a whole request, never a silently shortened excerpt. */
export const MAX_TEXT_CHARS = 4000;
export const MAX_MEDIA_BYTES = 25 * 1024 * 1024;

export function textProblem(text: string): string | null {
  if (!text.trim()) return "Enter the text you want to hear.";
  if (Array.from(text).length > MAX_TEXT_CHARS) return "Use 4,000 characters or fewer. Your text has not been shortened.";
  return null;
}

export function mediaProblem(file: Pick<File, "size"> | null): string | null {
  if (!file) return "Choose an audio or video file.";
  if (!file.size) return "That file is empty. Choose another file.";
  if (file.size > MAX_MEDIA_BYTES) return "Choose a file of 25 MiB or less.";
  return null;
}

export function audioFilename(type: string): string {
  const mime = type.split(";")[0].trim().toLowerCase();
  const extensions: Record<string, string> = {
    "audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/wav": "wav", "audio/x-wav": "wav",
    "audio/ogg": "ogg", "audio/webm": "webm", "audio/mp4": "m4a", "audio/aac": "aac",
    "audio/flac": "flac", "audio/aiff": "aiff", "audio/x-aiff": "aiff",
  };
  return `lens-speech.${extensions[mime] ?? "audio"}`;
}
