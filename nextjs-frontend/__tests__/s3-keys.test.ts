import { rcloneConfig, shareAddresses, webdavText } from "@/components/account/s3-model";

describe("Share addresses", () => {
  const page = { origin: "https://lens.example.com", hostname: "lens.example.com" };
  const ports = { port: 9555, webdav_port: 9556 };

  it("are the share servers' ports on this host", () => {
    expect(shareAddresses(page, ports, false)).toEqual({
      s3: "http://lens.example.com:9555",
      webdav: "http://lens.example.com:9556/",
    });
  });

  it("are the web app's own address once an admin opened them there", () => {
    expect(shareAddresses(page, ports, true)).toEqual({
      s3: "https://lens.example.com/s3",
      webdav: "https://lens.example.com/dav/",
    });
  });
});

describe("S3 access keys", () => {
  it("gives an rclone remote for the key", () => {
    expect(rcloneConfig("http://lens.local:9555", { id: "LKABC", secret: "s3cr3t" })).toBe(
      [
        "[lens]",
        "type = s3",
        "provider = Other",
        "access_key_id = LKABC",
        "secret_access_key = s3cr3t",
        "endpoint = http://lens.local:9555",
        "force_path_style = true",
      ].join("\n"),
    );
  });
});

describe("WebDAV sign-in", () => {
  it("gives the address, and the key and secret as user name and password", () => {
    expect(webdavText("http://lens.local:9556/", { id: "LKABC", secret: "s3cr3t" })).toBe(
      ["address  = http://lens.local:9556/", "user     = LKABC", "password = s3cr3t"].join("\n"),
    );
  });
});
