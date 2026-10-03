import { alsoKnownAs, entityQuery, filtersFrom, keptTypes, PAGE } from "@/components/entities/model";

describe("entities page model", () => {
  it("reads filters from the address, with defaults for anything odd", () => {
    const f = filtersFrom(new URLSearchParams("q=north&type=ORG&collection=7&view=hidden&sort=name&page=3"), "pods");
    expect(f).toEqual({
      ns: "pods",
      q: "north",
      type: "ORG",
      collection: 7,
      hidden: true,
      sort: "name",
      offset: 2 * PAGE,
    });
    const d = filtersFrom(new URLSearchParams("collection=x&sort=weird&page=-2"), "pods");
    expect(d).toMatchObject({ collection: null, hidden: false, sort: "mentions", offset: 0 });
  });

  it("sends only the filters that are set", () => {
    const q = entityQuery(filtersFrom(new URLSearchParams("q=%20%20"), "pods"));
    expect(q).toEqual({
      namespaces: "pods",
      q: undefined,
      types: undefined,
      collection: undefined,
      hidden: false,
      sort: "mentions",
      limit: PAGE,
      offset: 0,
    });
  });

  it("lists other names once, without the entity's own", () => {
    expect(
      alsoKnownAs({
        name: "Northwind Labs",
        key: "northwind labs",
        aliases: ["north wind labs", "northwind labs", "nwl", "nwl"],
      }),
    ).toEqual(["north wind labs", "nwl"]);
  });
});

describe("entity setup", () => {
  it("says which types a setup keeps", () => {
    const all = [
      { type: "ORG", label: "Organisation" },
      { type: "PLACE", label: "Place" },
      { type: "CLIENT", label: "Client" },
    ];
    expect(keptTypes([], all)).toBe("Every type");
    expect(keptTypes(["ORG"], all)).toBe("Organisation");
    expect(keptTypes(["ORG", "PLACE", "CLIENT"], all)).toBe("Organisation, Place and Client");
    expect(keptTypes(["GONE"], all)).toBe("GONE");
  });
});
