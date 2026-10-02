import {
  buildPayload,
  confirms,
  emptyForm,
  fileKind,
  healthOf,
  nextScan,
  parseRcloneToken,
  pickupText,
  sourceSubtitle,
  splitPatterns,
  suggestName,
  validateForm,
  WATCH_KINDS,
  watchSummary,
  type BackendSpec,
} from "@/components/sources/source-model";

const S3: BackendSpec = {
  label: "Amazon S3",
  fields: { provider: "AWS", region: "", endpoint: "", access_key_id: "" },
  secrets: ["secret_access_key"],
};
const SFTP: BackendSpec = {
  label: "SFTP",
  fields: { host: "", user: "", port: "22" },
  secrets: ["pass", "key_pem"],
};
const DROPBOX: BackendSpec = {
  label: "Dropbox",
  fields: {},
  secrets: ["token"],
  oauth: "rclone authorize dropbox",
};

describe("rclone token paste", () => {
  it("accepts the whole rclone output or just the JSON", () => {
    const out =
      'Paste the following into your remote machine --->\n{"access_token":"sl.B8x","token_type":"bearer","refresh_token":"Qe3","expiry":"2026-10-03T09:12:00Z"}\n<---End paste';
    const r = parseRcloneToken(out);
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(JSON.parse(r.token).access_token).toBe("sl.B8x");
      expect(r.refreshes).toBe(true);
      expect(r.expiry?.toISOString()).toBe("2026-10-03T09:12:00.000Z");
    }
  });
  it("explains what's wrong", () => {
    expect(parseRcloneToken("")).toEqual({ ok: null });
    expect(parseRcloneToken("sl.B8x…")).toMatchObject({
      ok: false,
      error: expect.stringContaining("including the braces"),
    });
    expect(parseRcloneToken('{"token_type":"bearer"}')).toMatchObject({
      ok: false,
      error: expect.stringContaining("access_token"),
    });
  });
});

describe("connections", () => {
  it("summarises each type in one line", () => {
    expect(
      sourceSubtitle({
        type: "s3",
        params: { provider: "AWS", region: "eu-central-1" },
      }),
    ).toBe("S3 · AWS eu-central-1");
    expect(
      sourceSubtitle({
        type: "sftp",
        params: { host: "calls-gw.internal", user: "recorder", port: "22" },
      }),
    ).toBe("SFTP · recorder@calls-gw.internal:22");
    expect(sourceSubtitle({ type: "drive", params: { scope: "drive.readonly" } })).toBe("Drive · read only");
    expect(sourceSubtitle({ type: "local", params: {} })).toBe("Folder on this machine");
  });
  it("shows health in the backend's words", () => {
    const now = new Date("2026-09-30T14:10:00");
    expect(healthOf({ ok: true, checked_at: "2026-09-30T14:02:00" }, now)).toEqual({
      tone: "ok",
      text: "OK · checked 14:02",
    });
    expect(
      healthOf(
        {
          ok: false,
          error: "SignatureDoesNotMatch: check your key and signing method",
        },
        now,
      ),
    ).toEqual({
      tone: "bad",
      text: "SignatureDoesNotMatch: check your key and signing method",
    });
    expect(healthOf(null).tone).toBe("unknown");
  });
  it("suggests a name from what was typed", () => {
    expect(suggestName("sftp", { host: "calls-gw.internal", user: "x" })).toBe("SFTP · calls-gw.internal");
    expect(suggestName("s3", { provider: "Wasabi", region: "eu-central-1" })).toBe("S3 · Wasabi eu-central-1");
    expect(suggestName("dropbox", {})).toBe("Dropbox");
  });
  it("validates the details before testing", () => {
    const f = emptyForm(S3);
    expect(Object.keys(validateForm("s3", S3, f, false))).toEqual(["access_key_id", "secret_access_key"]);
    const ok = {
      ...f,
      params: { ...f.params, access_key_id: "AKIA" },
      secrets: { secret_access_key: "s" },
    };
    expect(validateForm("s3", S3, ok, false)).toEqual({});
    // Editing: a saved secret counts.
    expect(validateForm("s3", S3, { ...ok, secrets: {} }, true, (k) => k === "secret_access_key")).toEqual({});
    expect(validateForm("dropbox", DROPBOX, emptyForm(DROPBOX), false)).toHaveProperty("token");
  });
  it("sends every parameter and only the secrets that change", () => {
    const f = {
      ...emptyForm(SFTP),
      params: { host: "h", user: "u", port: "22" },
      secrets: { key_pem: "KEY\r\nLINE" },
      sftpAuth: "key_pem" as const,
    };
    expect(buildPayload(SFTP, f, "sftp")).toEqual({
      params: { host: "h", user: "u", port: "22" },
      secrets: { key_pem: "KEY\nLINE" },
    });
    const clear = { ...f, secrets: { pass: "" } };
    expect(buildPayload(SFTP, clear, "sftp").secrets).toEqual({ pass: null });
    const tok = { ...emptyForm(DROPBOX), tokenText: '{"access_token":"a"}' };
    expect(buildPayload(DROPBOX, tok, "dropbox").secrets).toEqual({
      token: '{"access_token":"a"}',
    });
  });
  it("takes email accounts and calendar feeds", () => {
    const IMAP: BackendSpec = {
      label: "Email (IMAP)",
      fields: { host: "", port: "993", security: "ssl", user: "" },
      secrets: ["pass"],
    };
    const ICAL: BackendSpec = { label: "Calendar feed (iCal)", fields: { user: "" }, secrets: ["url", "pass"] };
    expect(sourceSubtitle({ type: "imap", params: { host: "imap.example.com", port: "993", user: "lens" } })).toBe(
      "IMAP · lens@imap.example.com:993",
    );
    expect(sourceSubtitle({ type: "ical", params: { user: "" } })).toBe("Calendar feed · address kept secret");
    expect(Object.keys(validateForm("imap", IMAP, emptyForm(IMAP), false))).toEqual(["host", "user", "pass"]);
    const mail = {
      ...emptyForm(IMAP),
      params: { host: "imap.example.com", port: "993", security: "ssl", user: "lens" },
    };
    expect(validateForm("imap", IMAP, { ...mail, secrets: { pass: "p" } }, false)).toEqual({});
    expect(validateForm("ical", ICAL, emptyForm(ICAL), false)).toHaveProperty("url");
    // the address is a secret: sent when typed, kept when editing without retyping it
    const cal = { ...emptyForm(ICAL), secrets: { url: "webcal://cal.example.org/team.ics" } };
    expect(validateForm("ical", ICAL, cal, false)).toEqual({});
    expect(validateForm("ical", ICAL, { ...cal, secrets: { url: "cal.example.org" } }, false)).toHaveProperty("url");
    expect(validateForm("ical", ICAL, emptyForm(ICAL), true, (k) => k === "url")).toEqual({});
    expect(buildPayload(ICAL, cal, "ical").secrets).toEqual({ url: "webcal://cal.example.org/team.ics" });
    expect(suggestName("ical", cal.params)).toBe("Calendar feed");
    expect([fileKind("Harbour report.eml"), fileKind("2026-10-02 Stand-up.ics")]).toEqual(["document", "transcript"]);
  });
  it("confirms deletes by name, loosely", () => {
    expect(confirms(" dropbox ·  callrecorder", "Dropbox · CallRecorder")).toBe(true);
    expect(confirms("Dropbox", "Dropbox · CallRecorder")).toBe(false);
    expect(confirms("", "")).toBe(false);
  });
});

describe("watched folders", () => {
  it("knows audio, transcripts, documents and images apart, and counts them", () => {
    expect(fileKind("ep12.M4A")).toBe("audio");
    expect(fileKind("ep12.srt")).toBe("transcript");
    expect([fileKind("notes.txt"), fileKind("letter.DOCX"), fileKind("mail.eml")]).toEqual([
      "document",
      "document",
      "document",
    ]);
    expect(fileKind("Report.PDF")).toBe("document"); // as new watched folders take it
    expect(fileKind("scan.tiff")).toBe("image");
    expect(fileKind("notes")).toBe("other");
    expect(pickupText({ files: 7, audio: 2, transcripts: 3, documents: 1, images: 1 })).toBe(
      "7 files · 2 audio · 3 transcripts · 1 document · 1 image",
    );
    expect(pickupText({ files: 1, audio: 0, transcripts: 1 })).toBe("1 file · 1 transcript");
    expect(pickupText({ files: 0, audio: 0, transcripts: 0, documents: 0, images: 0 })).toBe("0 files");
  });
  it("splits patterns and describes the settings", () => {
    expect(splitPatterns("*.m4a, *.srt,\n drafts/*")).toEqual(["*.m4a", "*.srt", "drafts/*"]);
    expect(
      watchSummary({
        namespace: "customer-calls",
        kinds: "audio",
        poll_minutes: 5,
        stable_seconds: 60,
        include: ["*.m4a"],
        exclude: [],
        steps: ["transcribe", "diarize"],
      }),
    ).toBe("→ customer-calls · audio only · every 5 min · wait 60 s · include *.m4a · steps: transcribe, diarize");
    expect(
      watchSummary(
        {
          namespace: "podcasts",
          kinds: "both",
          poll_minutes: 5,
          stable_seconds: 30,
        },
        "Podcast standard",
      ),
    ).toContain("pipeline: Podcast standard");
    // what each choice picks up; new folders take everything
    expect(WATCH_KINDS[0].value).toBe("all");
    expect(watchSummary({ namespace: "papers", kinds: "documents" })).toContain("documents and images only");
    expect(watchSummary({ namespace: "papers", kinds: "both" })).toContain("audio and transcripts");
    expect(watchSummary({ namespace: "papers" })).toContain("· everything ·");
  });
  it("says when the next scan is", () => {
    const now = Date.parse("2026-09-30T14:00:00Z");
    expect(nextScan("2026-09-30T14:04:00Z", true, now)).toBe("in 4 min");
    expect(nextScan("2026-09-30T13:59:00Z", true, now)).toBe("due now");
    expect(nextScan("2026-09-30T14:04:00Z", false, now)).toBe("paused");
  });
});
