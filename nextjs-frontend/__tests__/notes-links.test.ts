import { hrefFor, pageOf } from "@/components/notes/links";

describe("links in notes", () => {
  it("open the thing in the app", () => {
    expect(hrefFor("page:3")).toBe("/notes/3");
    expect(hrefFor("recording:12")).toBe("/resources/12");
    expect(hrefFor("entity:5", "pods")).toBe("/entities?entity=5&ns=pods");
    expect(hrefFor("topic:2", "pods")).toBe("/topics/2");
    expect(hrefFor("speaker:7")).toBe("/speakers/7");
    expect(hrefFor("collection:4", "pods")).toBe("/library?namespace=pods&collection=4");
  });

  it("ignore what isn't a link to Lens", () => {
    expect(hrefFor("https://example.com")).toBeNull();
    expect(hrefFor("entity:x")).toBeNull();
    expect(hrefFor("thing:3")).toBeNull();
  });

  it("find a thing's page", () => {
    expect(pageOf("entity:5")).toBe("/notes/about/entity/5");
  });
});
