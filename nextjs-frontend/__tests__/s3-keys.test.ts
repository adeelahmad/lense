import { rcloneConfig, webdavText } from "@/components/account/s3-model";

describe("S3 access keys", () => {
  it("gives an rclone remote for the key, at the address the web app is on", () => {
    expect(rcloneConfig("lens.local", { id: "LKABC", secret: "s3cr3t", port: 9555 })).toBe(
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
    expect(webdavText("lens.local", { id: "LKABC", secret: "s3cr3t", webdav_port: 9556 })).toBe(
      ["address  = http://lens.local:9556/", "user     = LKABC", "password = s3cr3t"].join("\n"),
    );
  });
});
