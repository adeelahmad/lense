import { embedUrl, fmtDay, formatStart, iframeSnippet, normalizeOrigin, originAllowed, parseStart, withStart } from "@/components/sharing/embed-model";

describe("start times", () => {
  it("reads m:ss, h:mm:ss and seconds", () => {
    expect(parseStart("14:02")).toBe(842);
    expect(parseStart("1:02:03")).toBe(3723);
    expect(parseStart("842")).toBe(842);
    expect(parseStart("")).toBe(0);
    expect(parseStart("14:2")).toBeNull();
    expect(parseStart("soon")).toBeNull();
    expect(formatStart(842)).toBe("14:02");
    expect(formatStart(3723)).toBe("1:02:03");
  });
});

describe("player address and snippet", () => {
  it("carries the share token and start", () => {
    expect(embedUrl("https://archive.example", 12, { token: "Kp2v", start: 842 })).toBe("https://archive.example/embed/12?s=Kp2v&t=842");
    expect(embedUrl("https://archive.example/", 12)).toBe("https://archive.example/embed/12");
  });
  it("changes the start on a signed link without touching the signature", () => {
    expect(withStart("/embed/1?t=5&exp=9&sig=abc", 842)).toBe("/embed/1?t=842&exp=9&sig=abc");
    expect(withStart("/embed/1?exp=9&sig=abc", 0)).toBe("/embed/1?exp=9&sig=abc");
  });
  it("builds an iframe with the size and an accessible title", () => {
    const s = iframeSnippet({ src: "https://a.example/embed/1?s=x&t=2", size: 360, title: 'Episode "12"' });
    expect(s).toContain('src="https://a.example/embed/1?s=x&t=2"');
    expect(s).toContain('width="360" height="520"');
    expect(s).toContain('title="Lens Archive player: Episode &quot;12&quot;"');
  });
});

describe("embed origins", () => {
  const self = "https://archive.lens.local";
  it("normalises what people type", () => {
    expect(normalizeOrigin("blog.halden-labs.com/post/1")).toBe("https://blog.halden-labs.com");
    expect(normalizeOrigin("http://localhost:8080/x")).toBe("http://localhost:8080");
    expect(normalizeOrigin("not a site")).toBeNull();
    expect(normalizeOrigin("ftp://files.example.com")).toBeNull();
  });
  it("matches CSP frame-ancestors sources", () => {
    expect(originAllowed("https://blog.halden-labs.com", ["'self'"], self)).toBe(false);
    expect(originAllowed(self, ["'self'"], self)).toBe(true);
    expect(originAllowed("https://blog.halden-labs.com", ["'self'", "https://blog.halden-labs.com"], self)).toBe(true);
    expect(originAllowed("https://blog.halden-labs.com", ["*.halden-labs.com"], self)).toBe(true);
    expect(originAllowed("https://halden-labs.com", ["https://*.halden-labs.com"], self)).toBe(false);
    expect(originAllowed("https://x.example", ["https:"], self)).toBe(true);
    expect(originAllowed("https://x.example", ["*"], self)).toBe(true);
    expect(originAllowed("https://x.example:8443", ["https://x.example"], self)).toBe(false);
    expect(originAllowed("https://x.example:8443", ["https://x.example:8443"], self)).toBe(true);
    expect(originAllowed("https://x.example", ["'none'"], self)).toBe(false);
  });
  it("writes dates like the design", () => {
    expect(fmtDay(new Date(2026, 9, 30))).toBe("30 Oct 2026");
  });
});
