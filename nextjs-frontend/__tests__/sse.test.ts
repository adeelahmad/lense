import { createSSEParser } from "@/lib/api/sse";

describe("createSSEParser", () => {
  it("parses events split across chunks", () => {
    const parser = createSSEParser();

    expect(parser.push('event: progress\nid: 1\ndata: {"pct"')).toEqual([]);
    expect(parser.push(": 50}\n\ndata: a\ndata: b\n\n")).toEqual([
      { event: "progress", id: "1", data: '{"pct": 50}', retry: undefined },
      { event: "message", id: "1", data: "a\nb", retry: undefined },
    ]);
  });

  it("handles CRLF line endings, including a CR/LF split across chunks", () => {
    const parser = createSSEParser();

    expect(parser.push("data: x\r")).toEqual([]);
    expect(parser.push("\n\r\n")).toEqual([{ event: "message", data: "x", id: undefined, retry: undefined }]);
  });

  it("ignores comments and events without data", () => {
    const parser = createSSEParser();

    expect(parser.push(": keep-alive\n\nevent: ping\n\nretry: 3000\ndata: ok\n\n")).toEqual([
      { event: "message", data: "ok", id: undefined, retry: 3000 },
    ]);
  });
});
