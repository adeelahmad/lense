import { readFileSync } from "fs";
import { join } from "path";

const css = readFileSync(join(__dirname, "..", "app", "globals.css"), "utf8");

describe("the app's stylesheet", () => {
  it("keeps Tailwind's utilities out of cascade layers, so a class beats the note editor's element rules", () => {
    // BlockSuite adds `input { font-size: 12px }` and the like to the page, unlayered: a layered utility would lose to it
    expect(css).toMatch(/@import "tailwindcss\/utilities\.css";/);
    expect(css).not.toMatch(/@import "tailwindcss";/);
    expect(css).not.toMatch(/@layer utilities/);
  });
});
