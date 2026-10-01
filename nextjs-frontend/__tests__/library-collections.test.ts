import type { CollectionNode } from "@/app/openapi-client/types.gen";
import {
  MAX_DEPTH,
  collectionName,
  collectionPath,
  defaultId,
  height,
  holds,
  homeText,
  libraryHref,
  moveTargets,
  pickerOptions,
  subtreeIds,
  whyNoDelete,
} from "@/components/library/collections-model";

/** podcasts: General (default) · Talks › 2024 › Spring · Talks › 2025 */
const node = (id: number, name: string, parent: number | null, depth: number, over: Partial<CollectionNode> = {}) =>
  ({
    id,
    name,
    parent,
    depth,
    space: 1,
    path: [],
    recordings: 0,
    total: 0,
    children: 0,
    default: false,
    ...over,
  }) as CollectionNode;

const tree: CollectionNode[] = [
  node(1, "General", null, 0, { default: true, recordings: 4, total: 4, path: ["General"] }),
  node(2, "Talks", null, 0, { recordings: 1, total: 6, children: 2, path: ["Talks"] }),
  node(3, "2024", 2, 1, { recordings: 2, total: 3, children: 1, path: ["Talks", "2024"] }),
  node(5, "Spring", 3, 2, { recordings: 1, total: 1, path: ["Talks", "2024", "Spring"] }),
  node(4, "2025", 2, 1, { recordings: 2, total: 2, path: ["Talks", "2025"] }),
];

describe("collections", () => {
  it("names one by its path", () => {
    expect(collectionPath(tree[3])).toBe("Talks › 2024 › Spring");
    expect(collectionPath({ name: "Loose", path: [] })).toBe("Loose");
    expect(collectionName(tree, 4)).toBe("2025");
    expect(collectionName(tree, 99)).toBeNull();
    expect(collectionName(undefined, 4)).toBeNull();
    expect(collectionName(tree, null)).toBeNull();
  });

  it("knows what's inside one and how deep it goes", () => {
    expect([...subtreeIds(tree, 2)].sort()).toEqual([2, 3, 4, 5]);
    expect([...subtreeIds(tree, 3)].sort()).toEqual([3, 5]);
    expect([...subtreeIds(tree, 1)]).toEqual([1]);
    expect(height(tree, 2)).toBe(2);
    expect(height(tree, 5)).toBe(0);
    expect(height(tree, 99)).toBe(0);
  });

  it("moves one only outside itself and never too deep", () => {
    expect(moveTargets(tree, 2).map((n) => n.id)).toEqual([1]);
    expect(moveTargets(tree, 3).map((n) => n.id)).toEqual([1, 2, 4]);
    expect(moveTargets(tree, 1).map((n) => n.id)).toEqual([2, 3, 5, 4]);
    // a collection with two levels below it fits under one MAX_DEPTH - 3 deep, not deeper
    const deep = [
      ...tree,
      node(10, "d", null, MAX_DEPTH - 4),
      node(11, "e", 10, MAX_DEPTH - 3),
      node(12, "f", 11, MAX_DEPTH - 2),
    ];
    expect(moveTargets(deep, 2).map((n) => n.id)).toEqual([1, 10]);
  });

  it("says why one can't be deleted", () => {
    expect(whyNoDelete(tree[0])).toMatch(/default collection/);
    expect(whyNoDelete(tree[1])).toBe("It holds 2 collections: move or delete them first");
    expect(whyNoDelete(tree[4])).toBe("It holds 2 recordings: move them to another collection first");
    expect(whyNoDelete({ default: false, children: 0, recordings: 0 })).toBeNull();
  });

  it("says what one holds", () => {
    expect(holds(tree[1])).toBe("1 recording · 5 inside");
    expect(holds(tree[4])).toBe("2 recordings");
    expect(holds({ recordings: 0, total: 0 })).toBe("0 recordings");
  });

  it("offers them indented, the default marked", () => {
    expect(pickerOptions(tree).map((o) => o.label)).toEqual([
      "General (default)",
      "Talks",
      "\u20032024",
      "\u2003\u2003Spring",
      "\u20032025",
    ]);
    expect(pickerOptions(undefined)).toEqual([]);
    expect(defaultId(tree)).toBe(1);
    expect(defaultId(null)).toBeNull();
  });

  it("links to the Library and says where a recording lives", () => {
    expect(libraryHref("podcasts")).toBe("/library?namespace=podcasts");
    expect(libraryHref("my talks", 3)).toBe("/library?namespace=my+talks&collection=3");
    const path = [
      { id: 2, name: "Talks" },
      { id: 3, name: "2024" },
    ];
    expect(homeText("podcasts", path)).toBe("podcasts › Talks › 2024");
    expect(homeText("podcasts", path, true)).toBe("podcasts › 2024");
    expect(homeText("podcasts", [])).toBe("podcasts");
    expect(homeText(null, undefined)).toBeNull();
  });
});
