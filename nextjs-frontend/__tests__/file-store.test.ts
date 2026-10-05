import { crossErrors, FIELDS, parse } from "@/components/settings/model";
import { fileStoreTry, NOT_STORAGE } from "@/components/storage/file-store";

const spec = (key: string) => FIELDS.find((f) => f.section === "files" && f.key === key)!;

describe("where Lens keeps its own files", () => {
  it("checks or saves what is chosen", () => {
    expect(fileStoreTry("local", "3", "x", true)).toEqual({ store: "local", crypt: false });
    expect(fileStoreTry("connection", "3", " my-bucket/lens ", 1)).toEqual({
      store: "connection",
      connection: 3,
      folder: "my-bucket/lens",
      crypt: true,
    });
    expect(fileStoreTry("connection", "", "", false)).toMatchObject({ connection: null });
  });

  it("parses the connection as an id, and asks for one", () => {
    expect(parse(spec("connection"), "12")).toEqual({ value: 12 });
    expect(parse(spec("connection"), "")).toEqual({ value: null });
    expect(crossErrors({ "files.store": "connection", "files.connection": null })["files.connection"]).toBeTruthy();
    expect(crossErrors({ "files.store": "local", "files.connection": null })["files.connection"]).toBeUndefined();
    expect(crossErrors({ "files.folder": "a/../b" })["files.folder"]).toBeTruthy();
  });

  it("never offers email or calendars as storage", () => {
    expect(NOT_STORAGE.has("imap") && NOT_STORAGE.has("ical") && !NOT_STORAGE.has("s3")).toBe(true);
  });
});
