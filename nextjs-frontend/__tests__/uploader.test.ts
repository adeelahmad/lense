import { Uploads } from "@/app/openapi-client";
import type { Client } from "@/app/openapi-client/client";
import { sendFile } from "@/components/import/uploader";

jest.mock("@/app/openapi-client", () => ({
  Uploads: { listUploads: jest.fn(), startUpload: jest.fn(), sendChunk: jest.fn(), getUpload: jest.fn() },
}));
jest.mock("next-auth/react", () => ({ useSession: jest.fn() }));

const MB = 1024 * 1024;
const SIZE = 2.5 * MB;
const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const fail = (status: number, detail = "nope") =>
  Promise.resolve({ error: { detail }, response: { ok: false, status } });
const up = (offset: number, over: object = {}) => ({
  id: "a".repeat(24),
  namespace: "pods",
  filename: "ep.m4a",
  size: SIZE,
  offset,
  state: offset >= SIZE ? "done" : "receiving",
  recording: offset >= SIZE ? 7 : null,
  job: offset >= SIZE ? 9 : null,
  duplicate: false,
  created_at: "",
  expires_at: "",
  ...over,
});
const m = (f: unknown) => f as jest.Mock;
const client = {} as Client;
const offsets = () => m(Uploads.sendChunk).mock.calls.map(([o]) => o.query.offset);

function send(signal = new AbortController().signal) {
  const file = new File([new Uint8Array(SIZE)], "ep.M4A");
  const progress: number[] = [];
  const run = sendFile(client, file, {
    namespace: "pods",
    pipeline: 3,
    collection: 12,
    title: "Episode",
    pieceMb: 1,
    signal,
    onProgress: (u) => progress.push(u.offset),
  });
  return { run, progress };
}

/** The server: each piece arrives, and the upload finishes with the last one. */
function serverTakesPieces() {
  m(Uploads.sendChunk).mockImplementation((o: { query: { offset: number }; body: Blob }) =>
    ok(up(o.query.offset + o.body.size)),
  );
}

describe("sending a file in pieces", () => {
  beforeEach(() => jest.useFakeTimers());
  afterEach(() => jest.useRealTimers());

  it("starts an upload and sends it a megabyte at a time", async () => {
    m(Uploads.listUploads).mockReturnValue(ok([]));
    m(Uploads.startUpload).mockReturnValue(ok(up(0)));
    serverTakesPieces();
    const { run, progress } = send();
    const done = await run;
    expect(m(Uploads.startUpload).mock.calls[0][0].body).toEqual({
      namespace: "pods",
      recording: null,
      pipeline: 3,
      collection: 12,
      filename: "ep.M4A",
      size: SIZE,
      title: "Episode",
      modified: expect.any(Number),
    });
    expect(offsets()).toEqual([0, MB, 2 * MB]);
    expect(m(Uploads.sendChunk).mock.calls.map(([o]) => o.body.size)).toEqual([MB, MB, 0.5 * MB]);
    expect(progress).toEqual([0, MB, 2 * MB, SIZE]);
    expect([done.state, done.recording, done.job]).toEqual(["done", 7, 9]);
  });

  it("uploads a transcript's audio for its recording", async () => {
    m(Uploads.listUploads).mockReturnValue(ok([up(MB)]));
    m(Uploads.startUpload).mockReturnValue(ok(up(0, { attach: 5 })));
    serverTakesPieces();
    const file = new File([new Uint8Array(SIZE)], "ep.m4a");
    await sendFile(client, file, {
      namespace: "pods",
      attach: 5,
      pipeline: 3,
      collection: 12,
      pieceMb: 1,
      signal: new AbortController().signal,
      onProgress: () => {},
    });
    // the unfinished upload of the same file was for a recording of its own, so this one starts afresh; attaching
    // runs its own steps and keeps the recording where it is, so no pipeline or collection goes with it
    expect(m(Uploads.startUpload).mock.calls[0][0].body).toMatchObject({
      recording: 5,
      pipeline: null,
      collection: null,
      filename: "ep.m4a",
    });
    expect(offsets()).toEqual([0, MB, 2 * MB]);
  });

  it("carries on an unfinished upload of the same file", async () => {
    m(Uploads.listUploads).mockReturnValue(ok([up(MB, { namespace: "calls" }), up(MB)]));
    serverTakesPieces();
    await send().run;
    expect(Uploads.startUpload).not.toHaveBeenCalled();
    expect(offsets()).toEqual([MB, 2 * MB]);
  });

  it("asks where it got to after a dropped connection, and carries on from there", async () => {
    m(Uploads.listUploads).mockReturnValue(ok([]));
    m(Uploads.startUpload).mockReturnValue(ok(up(0)));
    serverTakesPieces();
    m(Uploads.sendChunk)
      .mockImplementationOnce(() => Promise.reject(new TypeError("Failed to fetch")))
      .mockImplementationOnce(() => fail(409, "this upload has 1048576 bytes"));
    m(Uploads.getUpload)
      .mockReturnValueOnce(ok(up(0)))
      .mockReturnValueOnce(ok(up(MB)));
    const { run } = send();
    await jest.advanceTimersByTimeAsync(5000);
    const done = await run;
    expect(offsets()).toEqual([0, 0, MB, 2 * MB]);
    expect(Uploads.getUpload).toHaveBeenCalledTimes(2);
    expect(done.state).toBe("done");
  });

  it("stops at a refusal, after too many failures, and when paused", async () => {
    m(Uploads.listUploads).mockReturnValue(ok([]));
    m(Uploads.startUpload).mockReturnValue(ok(up(0)));
    m(Uploads.getUpload).mockReturnValue(ok(up(0)));

    m(Uploads.sendChunk).mockReturnValue(fail(400, "that's more than the file's size"));
    await expect(send().run).rejects.toMatchObject({ status: 400, message: "that's more than the file's size" });

    m(Uploads.sendChunk).mockReset().mockReturnValue(fail(503, "busy"));
    const tired = send().run;
    const settled = expect(tired).rejects.toMatchObject({ status: 503 });
    await jest.advanceTimersByTimeAsync(10 * 60_000);
    await settled;
    expect(Uploads.sendChunk).toHaveBeenCalledTimes(9);

    m(Uploads.sendChunk).mockReset().mockReturnValue(fail(502, "bad gateway"));
    const stop = new AbortController();
    const paused = send(stop.signal).run;
    const stopped = expect(paused).rejects.toBeDefined();
    await jest.advanceTimersByTimeAsync(100);
    stop.abort();
    await stopped;
    expect(Uploads.sendChunk).toHaveBeenCalledTimes(1);
  });
});
